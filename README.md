# Subtitulador

Sube un video, transcribe el audio a texto y descarga el video con subtítulos quemados.

## Características

- **Transcripción automática** con OpenAI Whisper (soporta 99 idiomas)
- **Traducción inteligente** de japonés, inglés y otros idiomas a español (o el que elijas)
- **Detección de idiomas mixtos** — maneja japonés + inglés en el mismo segmento
- **Filtro de alucinaciones** — elimina subtítulos falsos que Whisper inventa en silencio
- **Detección de silencio** — ajusta los timings para que los subtítulos no aparezcan cuando nadie habla
- **Interfaz drag & drop** — sube arrastrando o haz clic para seleccionar

## Requisitos

- Python 3.10+
- FFmpeg (debe estar en el PATH)

## Instalación

```bash
git clone https://github.com/Fedemaeda/Subtitulador.git
cd Subtitulador
pip install -r requirements.txt
```

## Uso

```bash
python app.py
```

Abre http://127.0.0.1:5000 en tu navegador.

1. Sube un video (MP4, MKV, AVI, MOV, WebM)
2. Selecciona el idioma del audio (o deja auto-detectar)
3. Elige el idioma de los subtítulos
4. Haz clic en **Transcribir y añadir subtítulos**
5. Descarga el video subtitulado

## Stack

- **Backend:** Python, Flask
- **Transcripción:** OpenAI Whisper
- **Traducción:** Google Translate (deep-translator)
- **Procesamiento de video:** FFmpeg
- **Frontend:** HTML, CSS, JavaScript (vanilla)
