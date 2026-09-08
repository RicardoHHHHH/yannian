# Third-party components

| Component | Purpose | License / upstream |
|---|---|---|
| Codex App Server | Local Codex connection through the user's existing installation; not bundled in the source archive | [Official integration documentation](https://learn.chatgpt.com/docs/app-server) |
| PyMuPDF | PDF text extraction and page rendering | AGPL-3.0 / commercial dual licensing, [upstream](https://github.com/pymupdf/PyMuPDF). This project uses the open-source distribution. |
| FastAPI | HTTP API | MIT, [upstream](https://github.com/fastapi/fastapi) |
| Uvicorn | ASGI server | BSD-3-Clause, [upstream](https://github.com/encode/uvicorn) |
| HTTPX | Outbound API requests | BSD-3-Clause, [upstream](https://github.com/encode/httpx) |
| PDF.js 6.3.289 | Original PDF canvas rendering, text selection, fonts and image codecs; legacy browser bundle with compatibility polyfills | Apache-2.0, [upstream release](https://github.com/mozilla/pdf.js/releases/tag/v6.3.289). License and supporting asset licenses are in `static/vendor/pdfjs/`. npm package SHA-512 verified when vendoring. |
| python-multipart | PDF upload forms | Apache-2.0, [upstream](https://github.com/Kludex/python-multipart) |
| python-dotenv | Local environment configuration | BSD-3-Clause, [upstream](https://github.com/theskumar/python-dotenv) |
| Marked | Markdown rendering | MIT. Bundled local distribution; license in `static/vendor/MARKED-LICENSE.md`. [Upstream](https://github.com/markedjs/marked) |
| DOMPurify 3.3.1 | Sanitize model-generated Markdown HTML | Apache-2.0 OR MPL-2.0. License in `static/vendor/DOMPURIFY-LICENSE`. [Upstream](https://github.com/cure53/DOMPurify) |

Python packages retain their own license files in their installed distributions. The source archive does not bundle a Python interpreter or virtual environment.

The Yannian book-and-spark logo in `static/yannian-logo.png` was generated for this project with imagegen. `yannian-app.ico` and `yannian-icon-64.png` are format/size derivatives of that artwork.
