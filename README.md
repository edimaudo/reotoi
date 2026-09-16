# reotoi

## Overview
reotoi is a web application that turns your voice into visual art.

The name comes from the Māori language: reo & toi which means voice art.

## Project Structure
```
reotoi/
├── main.py
├── requirements.txt
├── README.md
├── vercel.json
│
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── app.html
│   └── gallery.html
│
├── static/
│   ├── css/
│   │   └── styles.css
│   ├── js/
│   │   ├── app.js
│   │   ├── theme.js
│   │   └── gallery.js
│   └── assets/
│
├── services/
│   ├── __init__.py
│   ├── assemblyai_service.py
│   ├── voice_features.py
│   ├── art_generator.py
│   └── gallery_service.py
```
