# Boekhouding App

Simpele multi-tenant boekhoudtool voor ZZP'ers. Gebouwd met Flask + SQLite.

## Lokaal opstarten (backup op laptop)

```bash
cd boekhoud-app
python3 app.py
# Open http://localhost:5050
```

---

## GitHub instellen

```bash
cd boekhoud-app
git init
git add .
git commit -m "eerste versie"
# Maak een nieuwe repo aan op github.com, dan:
git remote add origin https://github.com/JOUW-NAAM/boekhoud-app.git
git push -u origin main
```

---

## Deployen op Hostinger VPS

### 1. VPS kopen
Koop een **VPS** op Hostinger (geen shared hosting — die draait alleen PHP).
Kies Ubuntu 22.04. Noteer je IP-adres.

### 2. Inloggen op de server
```bash
ssh root@JOUW-IP
```

### 3. Python en tools installeren
```bash
apt update && apt upgrade -y
apt install python3 python3-pip python3-venv nginx git -y
```

### 4. App downloaden van GitHub
```bash
mkdir -p /var/www/boekhoud
cd /var/www/boekhoud
git clone https://github.com/JOUW-NAAM/boekhoud-app.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
mkdir -p uploads
```

### 5. Environment variabelen instellen
```bash
cp .env.example .env
nano .env
# Vul SECRET_KEY in (genereer met: python3 -c "import secrets; print(secrets.token_hex(32))")
```

Laad de variabelen in de app — voeg bovenaan `app.py` toe:
```python
from dotenv import load_dotenv
load_dotenv()
```
En voeg `python-dotenv` toe aan requirements.txt.

### 6. Systemd service aanmaken (app altijd online)
```bash
nano /etc/systemd/system/boekhoud.service
```
Plak dit:
```ini
[Unit]
Description=Boekhoud App
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/boekhoud
Environment="PATH=/var/www/boekhoud/venv/bin"
EnvironmentFile=/var/www/boekhoud/.env
ExecStart=/var/www/boekhoud/venv/bin/gunicorn app:app --bind 127.0.0.1:8000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
systemctl daemon-reload
systemctl enable boekhoud
systemctl start boekhoud
systemctl status boekhoud   # check of het groen is
```

### 7. Nginx instellen (domein + HTTPS)
```bash
nano /etc/nginx/sites-available/boekhoud
```
Plak dit (vervang `jouwdomein.nl`):
```nginx
server {
    listen 80;
    server_name jouwdomein.nl www.jouwdomein.nl;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
```bash
ln -s /etc/nginx/sites-available/boekhoud /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 8. Domein koppelen in Hostinger
Ga naar Hostinger → DNS → voeg een **A-record** toe:
- Host: `@`
- Waarde: jouw VPS IP-adres

En nog een A-record:
- Host: `www`
- Waarde: jouw VPS IP-adres

DNS werkt binnen 5–30 minuten.

### 9. Gratis SSL (HTTPS) installeren
```bash
apt install certbot python3-certbot-nginx -y
certbot --nginx -d jouwdomein.nl -d www.jouwdomein.nl
```
Certbot regelt automatisch verlenging.

---

## Updates deployen (na wijzigingen op laptop)

```bash
# Op je laptop: push naar GitHub
git add .
git commit -m "update beschrijving"
git push

# Op de server:
ssh root@JOUW-IP
cd /var/www/boekhoud
git pull
systemctl restart boekhoud
```

---

## Gebruikers
- Eerste persoon die registreert wordt automatisch **beheerder**
- Beheerder kan gebruikers activeren/deactiveren via `/admin`
- Elke gebruiker ziet alleen zijn eigen data
