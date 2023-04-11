FROM python:3.10-slim

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

RUN mkdir /app
WORKDIR /app

RUN pip install --upgrade pip

ADD requirements.txt /app/

RUN pip install -r requirements.txt

ADD . /app

CMD [ "python3", "-m", "gunicorn", "-w", "4", "--bind", ":8000", "app:app" ]

