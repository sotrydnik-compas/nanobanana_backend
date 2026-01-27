# NanoBanana (FastAPI + Nginx + Docker)

Backend на **FastAPI** с проксированием через **nginx** и отдельным frontend (SPA), запускаемый через **Docker Compose**.

---

## 📦 Требования

- Docker **24+**
- Docker Compose **v2**
- Свободный порт **80**
- Собранный frontend (`dist/`)

---

## 📁 Структура проекта

├── Dockerfile

├── docker-compose.yml

├── .env

├── nginx/

│ └── nginx.conf

├── media/

├── logs/

└── backend (FastAPI код)

Frontend **не копируется в контейнер**, а монтируется с хоста.

---

## ⚙️ Переменные окружения (.env)

Создайте файл `.env` в корне проекта:

- API_KEY=CHANGE_ME_LONG_RANDOM
- CORS_ORIGINS=*
- PUBLIC_BASE_URL=https://your-domain-or-ip
- NANOBANANA_API_KEY=your_api_key
---

## 🖼 Frontend

Frontend должен быть **собран заранее**.

Пример пути, который используется в `docker-compose.yml` в nginx volumes:
- /home/compas/VSCodeProjects/nanobanana/nanobanana/dist

---

## 🚀 Запуск проекта

```bash
docker compose up --build -d
```

## 🌍 Доступные адреса

- Frontend (SPA)	http://localhost/widget/
- Backend API	http://localhost/api/
- Media файлы	http://localhost/media/<filename>