# Базовый образ
FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем pip и зависимости
RUN pip install --upgrade pip

COPY requirements.txt /app/
RUN pip install -r requirements.txt


# Копируем всё приложение
COPY . /app

# Создаём папку media
RUN mkdir -p /app/media /app/logs

# Экспортируем порт
EXPOSE 8000

# Запуск uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
