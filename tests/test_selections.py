import base64
import io
import json
import pymupdf as fitz
import pytest
from app import ai, db
from test_workbench import client, upload, pdf_bytes


def figure_pdf(rotation=0, crop=False):
    with fitz.open() as doc:
        page=doc.new_page(width=400,height=300)
        page.draw_rect(fitz.Rect(20,60,180,210),color=(1,0,0),fill=(1,0,0))
        page.draw_rect(fitz.Rect(220,60,380,210),color=(0,0,1),fill=(0,0,1))
        page.insert_text((30,35),'Red control and blue comparison',fontsize=14)
        if crop: page.set_cropbox(fitz.Rect(10,20,390,280))
        page.set_rotation(rotation)
        rect=fitz.Rect(20-(10 if crop else 0),60-(20 if crop else 0),180-(10 if crop else 0),210-(20 if crop else 0))*page.rotation_matrix
        normalized=[rect.x0/page.rect.width,rect.y0/page.rect.height,rect.x1/page.rect.width,rect.y1/page.rect.height]
        return doc.tobytes(),normalized


@pytest.mark.parametrize('rotation,crop',[(0,False),(90,False),(180,False),(270,False),(90,True)])
def test_region_crops_match_displayed_page_including_rotations_and_cropbox(client,rotation,crop):
    data,rect=figure_pdf(rotation,crop)
    paper=upload(client,data)
    result=client.post('/api/papers/'+paper['id']+'/selections',json={'page':1,'kind':'region','rects':[rect]} )
    assert result.status_code==200,result.text
    selection=result.json()
    image=client.get(selection['image_url'])
    pix=fitz.Pixmap(image.content)
    assert pix.pixel(pix.width//2,pix.height//2)[:3]==(255,0,0)
    assert pix.width in (450,480) and pix.height in (450,480)
    assert client.get('/api/papers/'+paper['id']+'/file').content==data
    assert client.get('/api/selections/'+selection['id']).json()['rects']==[rect]


def test_region_chat_sends_crop_and_restores_anchor_without_cross_selection_history(client,monkeypatch):
    data,rect=figure_pdf()
    p=upload(client,data)
    s=client.post('/api/papers/'+p['id']+'/selections',json={'page':1,'kind':'region','rects':[rect]}).json()
    recorded=[]
    async def answer(instructions,messages,**kwargs):
        recorded.append(messages[-1]['content'])
        return {'content':'Red control [S1]','citations':[],'provider':'api','model':'test'}
    monkeypatch.setattr(ai,'respond',answer)
    payload={'paper_id':p['id'],'paragraph_id':s['paragraph_id'],'selection_id':s['id'],'question':'What color is this region?','selected_text':'UNTRUSTED OVERRIDE'}
    result=client.post('/api/chat',json=payload)
    assert result.status_code==200,result.text
    image=next(c for c in recorded[0] if c['type']=='input_image')
    pix=fitz.Pixmap(base64.b64decode(image['image_url'].split(',',1)[1]))
    assert pix.pixel(pix.width//2,pix.height//2)[:3]==(255,0,0)
    assert pix.width<1000 and 'UNTRUSTED OVERRIDE' not in recorded[0][0]['text']
    result=result.json()
    assert result['citations'][-1]['selection_id']==s['id']
    history=client.get('/api/conversations/'+result['conversation_id']).json()
    assert history['conversation']['selection_id']==s['id']
    other=client.post('/api/papers/'+p['id']+'/selections',json={'page':1,'kind':'region','rects':[[.55,.2,.95,.7]]}).json()
    assert client.post('/api/chat',json={**payload,'selection_id':other['id'],'conversation_id':result['conversation_id']}).status_code==400
    second=upload(client,pdf_bytes('Different paper'))
    assert client.post('/api/chat',json={**payload,'paper_id':second['id']}).status_code==400
    idea=client.post('/api/ideas',json={'title':'Compare these two controls','body':'Check the figure','paper_id':p['id'],'selection_id':s['id']})
    assert idea.status_code==200 and idea.json()['selection_id']==s['id']


def test_text_selection_preserves_exact_quote_and_does_not_force_image(client,monkeypatch):
    p=upload(client)
    detail=client.get('/api/papers/'+p['id']).json()
    para=detail['paragraphs'][1]
    quote='retrieves evidence before producing an answer'
    s=client.post('/api/papers/'+p['id']+'/selections',json={'page':1,'kind':'text','rects':[para['display_bbox']],'text':quote}).json()
    assert s['paragraph_id']==para['id'] and s['text']==quote
    async def answer(instructions,messages,**kwargs):
        content=messages[-1]['content']
        assert quote in content[0]['text'] and len(content)==1
        return {'content':'Explanation','citations':[]}
    monkeypatch.setattr(ai,'respond',answer)
    assert client.post('/api/chat',json={'paper_id':p['id'],'paragraph_id':s['paragraph_id'],'selection_id':s['id'],'question':'Why?'}).status_code==200


@pytest.mark.parametrize('rects',[[[-.1,0,.5,.5]],[[0,0,1.1,.5]],[[.5,.5,.4,.7]],[[0,0,0,.5]]])
def test_invalid_selection_geometry_is_rejected(client,rects):
    p=upload(client)
    assert client.post('/api/papers/'+p['id']+'/selections',json={'page':1,'kind':'region','rects':rects}).status_code==400
    assert db.rows('SELECT * FROM selections')==[]
