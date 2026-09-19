web: python manage.py migrate --noinput && gunicorn unibooks.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4
