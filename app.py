import os, csv, io, hashlib
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, send_from_directory, make_response, session)
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'verander-dit-in-productie-met-random-string')
BASE = os.path.dirname(__file__)
DB_PATH = os.environ.get('DB_PATH', os.path.join(BASE, 'boekhoud.db'))
UPLOADS_DIR = os.environ.get('UPLOADS_DIR', os.path.join(BASE, 'uploads'))
os.makedirs(UPLOADS_DIR, exist_ok=True)


# ── Auth decorators ───────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            flash('Geen toegang — alleen voor beheerders.', 'danger')
            return redirect(url_for('transacties'))
        return f(*args, **kwargs)
    return decorated

def uid():
    return session['user_id']


# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# BTW-opties: (waarde_pct, label)
BTW_OPTIES = [
    ('0',    'Geen / Vrijgesteld'),
    ('21',   'Hoog 21% (af te dragen)'),
    ('9',    'Laag 9% (af te dragen)'),
    ('6',    'Laag 6% (af te dragen)'),
    ('0_eu_dienst', 'Verkopen (diensten) binnen de EU'),
    ('0_eu_goed',   'Verkopen (goederen) binnen de EU'),
    ('0_uitvoer',   'Leveringen naar landen buiten de EU (uitvoer)'),
]

# Haal het percentage uit een btw-waarde (bijv. '0_eu_dienst' → 0.0)
def btw_pct_van(waarde):
    try:
        return float(str(waarde).split('_')[0])
    except Exception:
        return 0.0

DEFAULT_REKENINGEN = [
    # code, naam, type, btw_waarde
    ('8000', 'Omzet diensten',           'omzet',   '21'),
    ('8010', 'Omzet producten',           'omzet',   '21'),
    ('8020', 'Omzet vrijgesteld',         'omzet',   '0'),
    ('8030', 'Omzet EU (diensten)',        'omzet',   '0_eu_dienst'),
    ('8040', 'Omzet EU (goederen)',        'omzet',   '0_eu_goed'),
    ('8050', 'Omzet export (buiten EU)',   'omzet',   '0_uitvoer'),
    ('4000', 'Kantoorkosten',             'kosten',  '21'),
    ('4010', 'Reiskosten',                'kosten',  '0'),
    ('4020', 'Autokosten',                'kosten',  '21'),
    ('4030', 'Marketing en reclame',      'kosten',  '21'),
    ('4040', 'Telefoon en internet',      'kosten',  '21'),
    ('4050', 'Software / abonnementen',   'kosten',  '21'),
    ('4060', 'Bankkosten',                'kosten',  '0'),
    ('4070', 'Representatiekosten',       'kosten',  '21'),
    ('4080', 'Opleiding en cursussen',    'kosten',  '21'),
    ('4090', 'Inkoop / materialen',       'kosten',  '21'),
    ('4100', 'Verzekeringen',             'kosten',  '0'),
    ('4110', 'Huur werkruimte',           'kosten',  '21'),
    ('1000', 'Bank',                      'activa',  '0'),
    ('1200', 'Debiteuren',                'activa',  '0'),
    ('1600', 'BTW te vorderen',           'activa',  '0'),
    ('1800', 'Nog te ontvangen omzet',    'activa',  '0'),
    ('1900', 'Overige vorderingen',       'activa',  '0'),
    ('2000', 'Crediteuren',               'passiva', '0'),
    ('2100', 'BTW te betalen',            'passiva', '0'),
    ('2200', 'Leningen',                  'passiva', '0'),
    ('9000', 'Privé-opname',              'prive',   '0'),
    ('9010', 'Privé-storting',            'prive',   '0'),
    ('0000', 'Overig / niet-zakelijk',    'nvt',     '0'),
]


def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                naam           TEXT    NOT NULL,
                email          TEXT    UNIQUE NOT NULL,
                wachtwoord     TEXT    NOT NULL,
                bedrijfsnaam   TEXT,
                is_admin       INTEGER DEFAULT 0,
                actief         INTEGER DEFAULT 1,
                aangemaakt     TEXT    DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS transacties (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                datum         TEXT    NOT NULL,
                omschrijving  TEXT,
                bedrag        REAL    NOT NULL,
                tegenrekening TEXT,
                geboekt       INTEGER DEFAULT 0,
                rekening_id   INTEGER,
                btw_waarde    TEXT    DEFAULT '0',
                factuur       TEXT,
                notitie       TEXT,
                hash          TEXT,
                UNIQUE(user_id, hash)
            );
            CREATE TABLE IF NOT EXISTS rekeningen (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                code       TEXT    NOT NULL,
                naam       TEXT    NOT NULL,
                type       TEXT    NOT NULL,
                btw_waarde TEXT    DEFAULT '0'
            );
            CREATE TABLE IF NOT EXISTS regels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                zoekterm    TEXT    NOT NULL,
                rekening_id INTEGER NOT NULL,
                btw_waarde  TEXT    DEFAULT '0'
            );
            CREATE TABLE IF NOT EXISTS klanten (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                naam        TEXT    NOT NULL,
                email       TEXT,
                adres       TEXT,
                postcode    TEXT,
                stad        TEXT,
                land        TEXT    DEFAULT 'Nederland',
                btw_nummer  TEXT,
                kvk_nummer  TEXT
            );
            CREATE TABLE IF NOT EXISTS facturen (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                klant_id        INTEGER,
                factuurnummer   TEXT    NOT NULL,
                factuurdatum    TEXT    NOT NULL,
                vervaldatum     TEXT,
                betaalprofiel   INTEGER DEFAULT 30,
                status          TEXT    DEFAULT 'concept',
                notitie         TEXT,
                bedragen_excl   INTEGER DEFAULT 1,
                aangemaakt      TEXT    DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS factuur_regels (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                factuur_id    INTEGER NOT NULL,
                omschrijving  TEXT,
                rekening_id   INTEGER,
                btw_waarde    TEXT    DEFAULT '21',
                aantal        REAL    DEFAULT 1,
                eenheidsprijs REAL    DEFAULT 0
            );
        ''')


def maak_standaard_rekeningen(user_id):
    with get_db() as conn:
        conn.executemany(
            'INSERT INTO rekeningen (user_id, code, naam, type, btw_waarde) VALUES (?,?,?,?,?)',
            [(user_id, code, naam, tp, btw) for code, naam, tp, btw in DEFAULT_REKENINGEN]
        )


def migreer_db():
    """Voegt nieuwe kolommen/tabellen toe aan bestaande databases zonder dataverlies."""
    with get_db() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transacties)").fetchall()]
        if 'factuur_id' not in cols:
            conn.execute('ALTER TABLE transacties ADD COLUMN factuur_id INTEGER')
        # Tabel met geleerde boekingen (onthoudt tegenpartij -> rubriek)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS geleerde_regels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                sleutel     TEXT    NOT NULL,
                rekening_id INTEGER,
                btw_waarde  TEXT    DEFAULT '0',
                auto_boek   INTEGER DEFAULT 0,
                UNIQUE(user_id, sleutel)
            )
        ''')


def leer_sleutel(naam):
    """Normaliseert een tegenpartij-naam tot een herkensleutel."""
    return (naam or '').strip().lower()[:80]


init_db()
migreer_db()


# ── Helpers ──────────────────────────────────────────────────────────────────

def kwartaal(datum_str):
    try:
        d = datetime.strptime(datum_str[:10], '%Y-%m-%d')
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    except Exception:
        return '?'


def btw_label(waarde):
    """Geeft het label terug voor een btw_waarde."""
    for v, l in BTW_OPTIES:
        if v == str(waarde):
            return l
    return str(waarde)


def netto_btw(bedrag, btw_waarde):
    """Given incl-BTW bedrag en btw_waarde, return (netto, btw)."""
    bedrag = abs(bedrag)
    pct = btw_pct_van(btw_waarde)
    if not pct:
        return round(bedrag, 2), 0.0
    factor = 1 + pct / 100
    netto = round(bedrag / factor, 2)
    btw = round(bedrag - netto, 2)
    return netto, btw


def parse_amount(s):
    s = str(s).strip().replace(' ', '')
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    return float(s)


def _normaliseer_datum(raw):
    """Zet allerlei datumnotaties om naar YYYY-MM-DD."""
    raw = (raw or '').strip()
    if not raw:
        return raw
    for fmt in ('%Y-%m-%d', '%Y%m%d', '%d-%m-%Y', '%d/%m/%Y', '%d-%m-%y', '%Y/%m/%d'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except Exception:
            continue
    return raw


def _vind_kolom(kolommen, *zoektermen):
    """Vind de eerste kolomnaam die een van de zoektermen bevat (niet-hoofdlettergevoelig)."""
    for zt in zoektermen:
        for k in kolommen:
            if zt in k.lower():
                return k
    return None


def detect_and_parse_csv(content):
    for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            text = content.decode(enc)
            break
        except Exception:
            text = None
    if not text:
        return []

    lines = text.strip().splitlines()
    if not lines:
        return []

    # Bepaal scheidingsteken op basis van de kopregel
    first = lines[0]
    delimiter = ';' if first.count(';') >= first.count(',') else ','
    if first.count('\t') > first.count(delimiter):
        delimiter = '\t'

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    try:
        rows = list(reader)
    except Exception:
        return []
    if not rows:
        return []

    kolommen = [k for k in rows[0].keys() if k]

    # Slim de juiste kolommen opzoeken (werkt voor Rabobank, ING, ABN, SNS, etc.)
    k_datum   = _vind_kolom(kolommen, 'transactiedatum', 'boekingsdatum', 'datum')
    k_bedrag  = _vind_kolom(kolommen, 'bedrag (eur)', 'transactiebedrag', 'bedrag')
    k_afbij   = _vind_kolom(kolommen, 'af bij', 'af/bij', 'debet/credit')
    k_naam    = _vind_kolom(kolommen, 'naam tegenpartij', 'tegenpartij naam',
                            'naam / omschrijving', 'naam tegenrekening')
    k_tegen   = _vind_kolom(kolommen, 'tegenrekening iban', 'tegenpartij iban',
                            'tegenrekening', 'tegenpartij')
    # Alle omschrijving/mededelingen-kolommen verzamelen (niet de naam-kolom zelf)
    k_omschr = [k for k in kolommen
                if ('omschrijving' in k.lower() or 'mededeling' in k.lower())
                and k != k_naam]

    transactions = []
    for r in rows:
        # Datum
        datum = _normaliseer_datum(r.get(k_datum, '') if k_datum else '')

        # Bedrag
        bedrag_raw = (r.get(k_bedrag, '') if k_bedrag else '').strip()
        if not bedrag_raw:
            continue
        try:
            bedrag = parse_amount(bedrag_raw)
        except Exception:
            continue
        # Af/Bij kolom (ING) bepaalt teken
        if k_afbij:
            af_bij = (r.get(k_afbij, '') or '').lower().strip()
            if af_bij in ('af', 'debet', 'd'):
                bedrag = -abs(bedrag)
            elif af_bij in ('bij', 'credit', 'c'):
                bedrag = abs(bedrag)

        # Naam tegenpartij (hoofdregel, dik)
        naam = (r.get(k_naam, '') if k_naam else '').strip()

        # Omschrijving samenstellen uit alle omschrijving-kolommen
        omschr_delen = []
        for k in k_omschr:
            v = (r.get(k, '') or '').strip()
            if v and v not in omschr_delen:
                omschr_delen.append(v)
        omschrijving_tekst = ' '.join(omschr_delen).strip()

        # Hoofdregel = naam; als geen naam, dan de omschrijving zelf
        hoofd = naam or omschrijving_tekst or '—'
        # Detailregel = omschrijving (als naam al de hoofdregel is)
        detail = omschrijving_tekst if naam else ''
        tegen_iban = (r.get(k_tegen, '') if k_tegen else '').strip()
        if tegen_iban and tegen_iban not in detail:
            detail = (detail + '  ·  ' + tegen_iban).strip(' ·') if detail else tegen_iban

        transactions.append({
            'datum': datum,
            'omschrijving': hoofd,
            'bedrag': bedrag,
            'tegenrekening': detail,
        })

    return transactions


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('transacties'))
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        ww = request.form['wachtwoord']
        with get_db() as conn:
            user = conn.execute('SELECT * FROM users WHERE email=? AND actief=1', (email,)).fetchone()
        if user and check_password_hash(user['wachtwoord'], ww):
            session['user_id'] = user['id']
            session['naam'] = user['naam']
            session['bedrijfsnaam'] = user['bedrijfsnaam'] or user['naam']
            session['is_admin'] = bool(user['is_admin'])
            return redirect(url_for('transacties'))
        flash('Onjuist e-mailadres of wachtwoord.', 'danger')
    return render_template('login.html')


@app.route('/registreren', methods=['GET', 'POST'])
def registreren():
    if 'user_id' in session:
        return redirect(url_for('transacties'))
    if request.method == 'POST':
        naam = request.form['naam'].strip()
        email = request.form['email'].strip().lower()
        bedrijf = request.form.get('bedrijfsnaam', '').strip()
        ww = request.form['wachtwoord']
        ww2 = request.form['wachtwoord2']
        if ww != ww2:
            flash('Wachtwoorden komen niet overeen.', 'danger')
            return render_template('registreren.html')
        if len(ww) < 8:
            flash('Wachtwoord moet minimaal 8 tekens bevatten.', 'danger')
            return render_template('registreren.html')
        with get_db() as conn:
            existing = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
            if existing:
                flash('Dit e-mailadres is al geregistreerd.', 'danger')
                return render_template('registreren.html')
            # First user becomes admin
            is_admin = 1 if not conn.execute('SELECT 1 FROM users LIMIT 1').fetchone() else 0
            user_id = conn.execute(
                'INSERT INTO users (naam, email, wachtwoord, bedrijfsnaam, is_admin) VALUES (?,?,?,?,?)',
                (naam, email, generate_password_hash(ww), bedrijf, is_admin)
            ).lastrowid
        maak_standaard_rekeningen(user_id)
        flash('Account aangemaakt! Je kunt nu inloggen.', 'success')
        return redirect(url_for('login'))
    return render_template('registreren.html')


@app.route('/uitloggen')
def uitloggen():
    session.clear()
    return redirect(url_for('login'))


# ── Transacties ───────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return redirect(url_for('transacties'))


@app.route('/transacties')
@login_required
def transacties():
    filter_mode = request.args.get('filter', 'onvolledig')
    kw_filter = request.args.get('kwartaal', '')

    with get_db() as conn:
        rekeningen = conn.execute(
            'SELECT * FROM rekeningen WHERE user_id=? ORDER BY code', (uid(),)
        ).fetchall()

        q = '''
            SELECT t.*, r.code AS rek_code, r.naam AS rek_naam, r.type AS rek_type
            FROM transacties t
            LEFT JOIN rekeningen r ON t.rekening_id = r.id
            WHERE t.user_id = ?
        '''
        params = [uid()]
        if filter_mode == 'onvolledig':
            q += ' AND t.geboekt = 0'
        elif filter_mode == 'geboekt':
            q += ' AND t.geboekt = 1'
        q += ' ORDER BY t.datum DESC, t.id DESC'

        rows = conn.execute(q, params).fetchall()
        if kw_filter:
            rows = [r for r in rows if kwartaal(r['datum']) == kw_filter]

        all_kw = sorted(set(
            kwartaal(r['datum']) for r in
            conn.execute('SELECT datum FROM transacties WHERE user_id=?', (uid(),))
        ), reverse=True)

        stats = {
            'onvolledig': conn.execute('SELECT COUNT(*) FROM transacties WHERE user_id=? AND geboekt=0', (uid(),)).fetchone()[0],
            'geboekt':    conn.execute('SELECT COUNT(*) FROM transacties WHERE user_id=? AND geboekt=1', (uid(),)).fetchone()[0],
        }

        regels = conn.execute(
            '''SELECT r.zoekterm, r.rekening_id, r.btw_waarde, rek.code, rek.naam
               FROM regels r JOIN rekeningen rek ON r.rekening_id = rek.id
               WHERE r.user_id=?''', (uid(),)
        ).fetchall()

        # Geleerde regels (tegenpartij -> rubriek) voor suggesties
        geleerd = conn.execute(
            '''SELECT g.sleutel, g.rekening_id, g.btw_waarde, g.auto_boek, rek.code, rek.naam
               FROM geleerde_regels g JOIN rekeningen rek ON g.rekening_id = rek.id
               WHERE g.user_id=?''', (uid(),)
        ).fetchall()

        # Open facturen om aan een betaling te koppelen
        facturen_open = conn.execute(
            '''SELECT f.id, f.factuurnummer, f.factuurdatum, k.naam AS klant_naam
               FROM facturen f LEFT JOIN klanten k ON f.klant_id = k.id
               WHERE f.user_id=? AND f.status != 'betaald'
               ORDER BY f.factuurdatum DESC''', (uid(),)
        ).fetchall()

    rekeningen_js = [dict(r) for r in rekeningen]
    regels_js = [dict(r) for r in regels]
    geleerd_js = [dict(r) for r in geleerd]
    facturen_js = [dict(r) for r in facturen_open]

    return render_template('transacties.html', transacties=rows, rekeningen=rekeningen,
                           filter_mode=filter_mode, kw_filter=kw_filter, all_kw=all_kw,
                           stats=stats, kwartaal_fn=kwartaal, btw_opties=BTW_OPTIES, btw_label=btw_label,
                           rekeningen_js=rekeningen_js, regels_js=regels_js,
                           geleerd_js=geleerd_js, facturen_js=facturen_js)


@app.route('/importeer', methods=['POST'])
@login_required
def importeer():
    f = request.files.get('bestand')
    if not f or not f.filename:
        flash('Geen bestand geselecteerd.', 'danger')
        return redirect(url_for('transacties'))

    parsed = detect_and_parse_csv(f.read())
    if not parsed:
        flash('Kon geen transacties lezen. Controleer het bestandsformaat (ING/Rabobank/ABN/CSV).', 'danger')
        return redirect(url_for('transacties'))

    imported = skipped = auto_geboekt = 0
    with get_db() as conn:
        regels = conn.execute('SELECT * FROM regels WHERE user_id=?', (uid(),)).fetchall()
        geleerd = {g['sleutel']: g for g in conn.execute(
            'SELECT * FROM geleerde_regels WHERE user_id=? AND auto_boek=1', (uid(),)).fetchall()}
        for t in parsed:
            h = hashlib.md5(f"{uid()}|{t['datum']}|{t['omschrijving']}|{t['bedrag']}".encode()).hexdigest()
            rekening_id = None
            btw_waarde = '0'
            # 1) Geleerde auto-boekregel (exacte tegenpartij) heeft voorrang
            gl = geleerd.get(leer_sleutel(t['omschrijving']))
            if gl:
                rekening_id = gl['rekening_id']
                btw_waarde = gl['btw_waarde']
                auto_geboekt += 1
            else:
                # 2) Handmatige zoekterm-regels
                for regel in regels:
                    if regel['zoekterm'].lower() in (t['omschrijving'] or '').lower():
                        rekening_id = regel['rekening_id']
                        btw_waarde = regel['btw_waarde']
                        break
            geboekt = 1 if rekening_id else 0
            try:
                conn.execute(
                    'INSERT INTO transacties (user_id,datum,omschrijving,bedrag,tegenrekening,rekening_id,btw_waarde,geboekt,hash) VALUES (?,?,?,?,?,?,?,?,?)',
                    (uid(), t['datum'], t['omschrijving'], t['bedrag'],
                     t.get('tegenrekening', ''), rekening_id, btw_waarde, geboekt, h)
                )
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1

    extra = f' waarvan {auto_geboekt} automatisch geboekt' if auto_geboekt else ''
    flash(f'✓ {imported} transacties geïmporteerd{extra}, {skipped} overgeslagen (al aanwezig).', 'success')
    return redirect(url_for('transacties'))


@app.route('/boek/<int:tid>', methods=['POST'])
@login_required
def boek(tid):
    with get_db() as conn:
        t = conn.execute('SELECT * FROM transacties WHERE id=? AND user_id=?', (tid, uid())).fetchone()
        if not t:
            flash('Niet gevonden.', 'danger')
            return redirect(url_for('transacties'))

        rekening_id = request.form.get('rekening_id') or None
        btw_waarde = request.form.get('btw_waarde') or '0'
        notitie = request.form.get('notitie', '')
        factuur_id = request.form.get('factuur_id') or None
        onthoud = request.form.get('onthoud') == '1'
        auto_boek = request.form.get('auto_boek') == '1'
        factuur = t['factuur']

        f = request.files.get('factuur')
        if f and f.filename:
            filename = f"{uid()}_{tid}_{f.filename}"
            f.save(os.path.join(UPLOADS_DIR, filename))
            factuur = filename

        geboekt = 1 if rekening_id else 0
        conn.execute(
            'UPDATE transacties SET rekening_id=?, btw_waarde=?, notitie=?, factuur=?, factuur_id=?, geboekt=? WHERE id=? AND user_id=?',
            (rekening_id, btw_waarde, notitie, factuur, factuur_id, geboekt, tid, uid())
        )

        # Gekoppelde factuur op 'betaald' zetten
        if factuur_id:
            conn.execute("UPDATE facturen SET status='betaald' WHERE id=? AND user_id=?",
                         (factuur_id, uid()))

        # Onthoud deze boeking voor toekomstige transacties van dezelfde tegenpartij
        if (onthoud or auto_boek) and rekening_id:
            sleutel = leer_sleutel(t['omschrijving'])
            if sleutel:
                conn.execute(
                    '''INSERT INTO geleerde_regels (user_id, sleutel, rekening_id, btw_waarde, auto_boek)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(user_id, sleutel) DO UPDATE SET
                         rekening_id=excluded.rekening_id,
                         btw_waarde=excluded.btw_waarde,
                         auto_boek=excluded.auto_boek''',
                    (uid(), sleutel, rekening_id, btw_waarde, 1 if auto_boek else 0)
                )
    return redirect(request.referrer or url_for('transacties'))


@app.route('/verwijder/<int:tid>', methods=['POST'])
@login_required
def verwijder(tid):
    with get_db() as conn:
        conn.execute('DELETE FROM transacties WHERE id=? AND user_id=?', (tid, uid()))
    flash('Transactie verwijderd.', 'info')
    return redirect(request.referrer or url_for('transacties'))


@app.route('/verwijder-meerdere', methods=['POST'])
@login_required
def verwijder_meerdere():
    ids = request.form.getlist('ids')
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        flash('Geen transacties geselecteerd.', 'warning')
        return redirect(request.referrer or url_for('transacties'))
    with get_db() as conn:
        placeholders = ','.join('?' for _ in ids)
        conn.execute(
            f'DELETE FROM transacties WHERE user_id=? AND id IN ({placeholders})',
            [uid()] + ids
        )
    flash(f'{len(ids)} transactie(s) verwijderd.', 'info')
    return redirect(request.referrer or url_for('transacties'))


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    # Verify file belongs to this user (filename starts with user_id_)
    if not filename.startswith(f"{uid()}_"):
        flash('Geen toegang.', 'danger')
        return redirect(url_for('transacties'))
    return send_from_directory(UPLOADS_DIR, filename)


# ── Grootboekrekeningen ───────────────────────────────────────────────────────

@app.route('/grootboek')
@login_required
def grootboek():
    with get_db() as conn:
        rekeningen = conn.execute(
            'SELECT * FROM rekeningen WHERE user_id=? ORDER BY code', (uid(),)
        ).fetchall()
    return render_template('grootboek.html', rekeningen=rekeningen, btw_opties=BTW_OPTIES, btw_label=btw_label)


@app.route('/grootboek/toevoegen', methods=['POST'])
@login_required
def grootboek_toevoegen():
    code = request.form['code'].strip()
    naam = request.form['naam'].strip()
    type_ = request.form['type']
    btw_waarde = request.form.get('btw_waarde') or '0'
    with get_db() as conn:
        conn.execute('INSERT INTO rekeningen (user_id,code,naam,type,btw_waarde) VALUES (?,?,?,?,?)',
                     (uid(), code, naam, type_, btw_waarde))
    flash(f'Rekening {code} {naam} toegevoegd.', 'success')
    return redirect(url_for('grootboek'))


@app.route('/grootboek/bewerken/<int:rid>', methods=['POST'])
@login_required
def grootboek_bewerken(rid):
    naam = request.form['naam'].strip()
    btw_waarde = request.form.get('btw_waarde') or '0'
    with get_db() as conn:
        conn.execute('UPDATE rekeningen SET naam=?, btw_waarde=? WHERE id=? AND user_id=?',
                     (naam, btw_waarde, rid, uid()))
    flash('Rekening bijgewerkt.', 'success')
    return redirect(url_for('grootboek'))


@app.route('/grootboek/verwijder/<int:rid>', methods=['POST'])
@login_required
def grootboek_verwijder(rid):
    with get_db() as conn:
        in_use = conn.execute(
            'SELECT COUNT(*) FROM transacties WHERE rekening_id=? AND user_id=?', (rid, uid())
        ).fetchone()[0]
        if in_use:
            flash('Kan niet verwijderen: rekening is in gebruik.', 'danger')
        else:
            conn.execute('DELETE FROM rekeningen WHERE id=? AND user_id=?', (rid, uid()))
            flash('Rekening verwijderd.', 'info')
    return redirect(url_for('grootboek'))


# ── Auto-regels ───────────────────────────────────────────────────────────────

@app.route('/auto-regels')
@login_required
def auto_regels():
    with get_db() as conn:
        regels = conn.execute(
            '''SELECT r.*, rek.code, rek.naam
               FROM regels r JOIN rekeningen rek ON r.rekening_id=rek.id
               WHERE r.user_id=? ORDER BY r.zoekterm''', (uid(),)
        ).fetchall()
        rekeningen = conn.execute(
            'SELECT * FROM rekeningen WHERE user_id=? ORDER BY code', (uid(),)
        ).fetchall()
    return render_template('auto_regels.html', regels=regels, rekeningen=rekeningen, btw_opties=BTW_OPTIES, btw_label=btw_label)


@app.route('/auto-regels/toevoegen', methods=['POST'])
@login_required
def auto_regels_toevoegen():
    zoekterm = request.form['zoekterm'].strip()
    rekening_id = request.form['rekening_id']
    btw_waarde = request.form.get('btw_waarde') or '0'
    with get_db() as conn:
        conn.execute('INSERT INTO regels (user_id,zoekterm,rekening_id,btw_waarde) VALUES (?,?,?,?)',
                     (uid(), zoekterm, rekening_id, btw_waarde))
    flash(f'Automatische regel toegevoegd voor "{zoekterm}".', 'success')
    return redirect(url_for('auto_regels'))


@app.route('/auto-regels/verwijder/<int:rid>', methods=['POST'])
@login_required
def auto_regels_verwijder(rid):
    with get_db() as conn:
        conn.execute('DELETE FROM regels WHERE id=? AND user_id=?', (rid, uid()))
    flash('Regel verwijderd.', 'info')
    return redirect(url_for('auto_regels'))


# ── BTW rapport ───────────────────────────────────────────────────────────────

@app.route('/btw-rapport')
@login_required
def btw_rapport():
    kw_sel = request.args.get('kwartaal', '')
    with get_db() as conn:
        geboekt = conn.execute(
            '''SELECT t.*, r.code, r.naam, r.type
               FROM transacties t JOIN rekeningen r ON t.rekening_id=r.id
               WHERE t.geboekt=1 AND t.user_id=?''', (uid(),)
        ).fetchall()

    per_kw = {}
    for t in geboekt:
        per_kw.setdefault(kwartaal(t['datum']), []).append(t)
    all_kw = sorted(per_kw.keys(), reverse=True)
    if not kw_sel and all_kw:
        kw_sel = all_kw[0]

    data = None
    if kw_sel and kw_sel in per_kw:
        trans = per_kw[kw_sel]
        btw_omzet = btw_kosten = 0.0
        for t in trans:
            _, btw = netto_btw(t['bedrag'], t['btw_waarde'])
            if t['type'] == 'inkomsten':
                btw_omzet += btw
            elif t['type'] == 'kosten':
                btw_kosten += btw
        data = {
            'kwartaal': kw_sel,
            'btw_omzet': round(btw_omzet, 2),
            'btw_kosten': round(btw_kosten, 2),
            'te_betalen': round(btw_omzet - btw_kosten, 2),
            'transacties': trans,
        }
    return render_template('btw_rapport.html', data=data, all_kw=all_kw, kw_sel=kw_sel, netto_btw=netto_btw, btw_label=btw_label)


# ── Kwartaalrapport ───────────────────────────────────────────────────────────

@app.route('/kwartaalrapport')
@login_required
def kwartaalrapport():
    kw_sel = request.args.get('kwartaal', '')
    with get_db() as conn:
        geboekt = conn.execute(
            '''SELECT t.*, r.code, r.naam, r.type
               FROM transacties t JOIN rekeningen r ON t.rekening_id=r.id
               WHERE t.geboekt=1 AND t.user_id=? ORDER BY t.datum''', (uid(),)
        ).fetchall()

    per_kw = {}
    for t in geboekt:
        per_kw.setdefault(kwartaal(t['datum']), []).append(t)
    all_kw = sorted(per_kw.keys(), reverse=True)
    if not kw_sel and all_kw:
        kw_sel = all_kw[0]

    rapport = None
    if kw_sel and kw_sel in per_kw:
        trans = per_kw[kw_sel]
        per_rek = {}
        for t in trans:
            key = (t['code'], t['naam'], t['type'])
            if key not in per_rek:
                per_rek[key] = {'trans': [], 'netto': 0.0, 'btw': 0.0}
            n, b = netto_btw(t['bedrag'], t['btw_waarde'])
            per_rek[key]['trans'].append(t)
            per_rek[key]['netto'] += n
            per_rek[key]['btw'] += b
        tot_ink = sum(v['netto'] for (_, _, tp), v in per_rek.items() if tp == 'inkomsten')
        tot_kst = sum(v['netto'] for (_, _, tp), v in per_rek.items() if tp == 'kosten')
        btw_omz = sum(v['btw'] for (_, _, tp), v in per_rek.items() if tp == 'inkomsten')
        btw_kst = sum(v['btw'] for (_, _, tp), v in per_rek.items() if tp == 'kosten')
        rapport = {
            'kwartaal': kw_sel,
            'per_rek': dict(sorted(per_rek.items(), key=lambda x: x[0][0])),
            'tot_inkomsten': round(tot_ink, 2),
            'tot_kosten': round(tot_kst, 2),
            'resultaat': round(tot_ink - tot_kst, 2),
            'btw_omzet': round(btw_omz, 2),
            'btw_kosten': round(btw_kst, 2),
            'btw_te_betalen': round(btw_omz - btw_kst, 2),
            'alle_trans': trans,
        }
    return render_template('kwartaalrapport.html', rapport=rapport, all_kw=all_kw,
                           kw_sel=kw_sel, netto_btw=netto_btw, btw_label=btw_label,
                           now=datetime.now().strftime('%d-%m-%Y %H:%M'))


@app.route('/kwartaalrapport/export/<kw_str>')
@login_required
def kwartaalrapport_export(kw_str):
    with get_db() as conn:
        trans = conn.execute(
            '''SELECT t.*, r.code, r.naam, r.type
               FROM transacties t JOIN rekeningen r ON t.rekening_id=r.id
               WHERE t.geboekt=1 AND t.user_id=? ORDER BY t.datum''', (uid(),)
        ).fetchall()
    trans = [t for t in trans if kwartaal(t['datum']) == kw_str]
    if not trans:
        return "Geen geboekte transacties voor dit kwartaal.", 404

    per_rek = {}
    for t in trans:
        key = (t['code'], t['naam'], t['type'])
        per_rek.setdefault(key, []).append(t)

    lines = []
    lines += [f"KWARTAALRAPPORT {kw_str}",
              f"Bedrijf: {session.get('bedrijfsnaam', '')}",
              f"Gegenereerd op: {datetime.now().strftime('%d-%m-%Y %H:%M')}",
              "=" * 70, ""]

    tot_ink = tot_kst = btw_omz = btw_kst = 0.0
    for label, tp_key in [("INKOMSTEN", "inkomsten"), ("KOSTEN", "kosten"), ("PRIVÉ", "prive"), ("OVERIG", "nvt")]:
        items = [(k, v) for k, v in per_rek.items() if k[2] == tp_key]
        if not items:
            continue
        lines.append(f"── {label} " + "─" * (60 - len(label)))
        for (code, naam, _), ts in sorted(items, key=lambda x: x[0][0]):
            tn = tb = 0.0
            for t in ts:
                n, b = netto_btw(t['bedrag'], t['btw_waarde'])
                tn += n; tb += b
            tn, tb = round(tn, 2), round(tb, 2)
            lines.append(f"  {code}  {naam:<32}  Netto: € {tn:>10.2f}   BTW: € {tb:>8.2f}")
            if tp_key == 'inkomsten':
                tot_ink += tn; btw_omz += tb
            elif tp_key == 'kosten':
                tot_kst += tn; btw_kst += tb
        lines.append("")

    lines += ["=" * 70,
              f"TOTAAL INKOMSTEN (excl. BTW):  € {round(tot_ink,2):>10.2f}",
              f"TOTAAL KOSTEN    (excl. BTW):  € {round(tot_kst,2):>10.2f}",
              f"RESULTAAT:                     € {round(tot_ink-tot_kst,2):>10.2f}", "",
              "── BTW OVERZICHT " + "─" * 52,
              f"  BTW over omzet:      € {round(btw_omz,2):>8.2f}",
              f"  BTW over kosten:     € {round(btw_kst,2):>8.2f}",
              f"  SALDO (te betalen):  € {round(btw_omz-btw_kst,2):>8.2f}", "",
              "── DETAILS PER TRANSACTIE " + "─" * 44,
              f"{'Datum':<12} {'Omschrijving':<38} {'Bedrag':>10}  {'Rekening':<34} {'BTW%':>4}  {'BTW€':>8}",
              "-" * 112]
    for t in trans:
        _, b = netto_btw(t['bedrag'], t['btw_waarde'])
        lines.append(
            f"{t['datum']:<12} {(t['omschrijving'] or '')[:37]:<38} "
            f"€ {t['bedrag']:>8.2f}  {t['code']} {t['naam'][:27]:<34} "
            f"{btw_label(t['btw_waarde']):<24}  € {b:>6.2f}"
        )
    lines.append(f"\nTotaal {len(trans)} transacties.")

    resp = make_response('\n'.join(lines))
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename=kwartaalrapport-{kw_str}.txt'
    return resp


# ── Admin ────────────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin():
    with get_db() as conn:
        users = conn.execute('SELECT * FROM users ORDER BY aangemaakt').fetchall()
        stats = []
        for u in users:
            t_count = conn.execute('SELECT COUNT(*) FROM transacties WHERE user_id=?', (u['id'],)).fetchone()[0]
            stats.append({'user': u, 'transacties': t_count})
    return render_template('admin.html', stats=stats)


@app.route('/admin/toggle/<int:user_id>', methods=['POST'])
@admin_required
def admin_toggle(user_id):
    if user_id == uid():
        flash('Je kunt je eigen account niet deactiveren.', 'danger')
        return redirect(url_for('admin'))
    with get_db() as conn:
        user = conn.execute('SELECT actief FROM users WHERE id=?', (user_id,)).fetchone()
        if user:
            conn.execute('UPDATE users SET actief=? WHERE id=?', (0 if user['actief'] else 1, user_id))
    return redirect(url_for('admin'))


# ── Klanten ───────────────────────────────────────────────────────────────────

@app.route('/klanten')
@login_required
def klanten():
    with get_db() as conn:
        k = conn.execute('SELECT * FROM klanten WHERE user_id=? ORDER BY naam', (uid(),)).fetchall()
    return render_template('klanten.html', klanten=k)


@app.route('/klanten/toevoegen', methods=['POST'])
@login_required
def klanten_toevoegen():
    with get_db() as conn:
        conn.execute(
            'INSERT INTO klanten (user_id,naam,email,adres,postcode,stad,land,btw_nummer,kvk_nummer) VALUES (?,?,?,?,?,?,?,?,?)',
            (uid(), request.form['naam'].strip(), request.form.get('email','').strip(),
             request.form.get('adres','').strip(), request.form.get('postcode','').strip(),
             request.form.get('stad','').strip(), request.form.get('land','Nederland').strip(),
             request.form.get('btw_nummer','').strip(), request.form.get('kvk_nummer','').strip())
        )
    flash('Klant toegevoegd.', 'success')
    return redirect(request.referrer or url_for('klanten'))


@app.route('/klanten/bewerken/<int:kid>', methods=['POST'])
@login_required
def klanten_bewerken(kid):
    with get_db() as conn:
        conn.execute(
            'UPDATE klanten SET naam=?,email=?,adres=?,postcode=?,stad=?,land=?,btw_nummer=?,kvk_nummer=? WHERE id=? AND user_id=?',
            (request.form['naam'].strip(), request.form.get('email','').strip(),
             request.form.get('adres','').strip(), request.form.get('postcode','').strip(),
             request.form.get('stad','').strip(), request.form.get('land','Nederland').strip(),
             request.form.get('btw_nummer','').strip(), request.form.get('kvk_nummer','').strip(),
             kid, uid())
        )
    flash('Klant bijgewerkt.', 'success')
    return redirect(url_for('klanten'))


@app.route('/klanten/verwijder/<int:kid>', methods=['POST'])
@login_required
def klanten_verwijder(kid):
    with get_db() as conn:
        in_use = conn.execute('SELECT COUNT(*) FROM facturen WHERE klant_id=? AND user_id=?', (kid, uid())).fetchone()[0]
        if in_use:
            flash('Kan niet verwijderen: klant heeft facturen.', 'danger')
        else:
            conn.execute('DELETE FROM klanten WHERE id=? AND user_id=?', (kid, uid()))
            flash('Klant verwijderd.', 'info')
    return redirect(url_for('klanten'))


# ── Facturen helpers ──────────────────────────────────────────────────────────

def volgend_factuurnummer(user_id):
    jaar = datetime.now().year
    with get_db() as conn:
        rij = conn.execute(
            "SELECT factuurnummer FROM facturen WHERE user_id=? AND factuurnummer LIKE ? ORDER BY id DESC LIMIT 1",
            (user_id, f'{jaar}.%')
        ).fetchone()
    if rij:
        try:
            n = int(rij['factuurnummer'].split('.')[1]) + 1
        except Exception:
            n = 1
    else:
        n = 1
    return f"{jaar}.{n}"


def bereken_factuur_totalen(regels, bedragen_excl):
    """Berekent totalen voor een lijst van regelrijen (sqlite Row of dict)."""
    totaal_excl = 0.0
    btw_per_code = {}
    for r in regels:
        aantal = float(r['aantal'] or 1)
        prijs = float(r['eenheidsprijs'] or 0)
        bw = r['btw_waarde'] or '0'
        pct = btw_pct_van(bw)
        if bedragen_excl:
            excl = round(aantal * prijs, 2)
            btw_b = round(excl * pct / 100, 2)
        else:
            incl = round(aantal * prijs, 2)
            excl = round(incl / (1 + pct / 100), 2) if pct else incl
            btw_b = round(incl - excl, 2)
        totaal_excl += excl
        label = btw_label(bw)
        btw_per_code[label] = round(btw_per_code.get(label, 0) + btw_b, 2)
    totaal_btw = round(sum(btw_per_code.values()), 2)
    totaal_incl = round(totaal_excl + totaal_btw, 2)
    return round(totaal_excl, 2), btw_per_code, totaal_btw, totaal_incl


# ── Facturen routes ───────────────────────────────────────────────────────────

@app.route('/facturen')
@login_required
def facturen():
    with get_db() as conn:
        rows = conn.execute(
            '''SELECT f.*, k.naam AS klant_naam
               FROM facturen f LEFT JOIN klanten k ON f.klant_id = k.id
               WHERE f.user_id=? ORDER BY f.factuurdatum DESC, f.id DESC''',
            (uid(),)
        ).fetchall()
        stats = {
            'concept':    conn.execute("SELECT COUNT(*) FROM facturen WHERE user_id=? AND status='concept'",   (uid(),)).fetchone()[0],
            'vastgelegd': conn.execute("SELECT COUNT(*) FROM facturen WHERE user_id=? AND status='vastgelegd'",(uid(),)).fetchone()[0],
            'betaald':    conn.execute("SELECT COUNT(*) FROM facturen WHERE user_id=? AND status='betaald'",   (uid(),)).fetchone()[0],
        }
    return render_template('facturen.html', facturen=rows, stats=stats,
                           now=datetime.now().strftime('%Y-%m-%d'))


@app.route('/facturen/nieuw', methods=['GET', 'POST'])
@login_required
def facturen_nieuw():
    with get_db() as conn:
        kl = conn.execute('SELECT * FROM klanten WHERE user_id=? ORDER BY naam', (uid(),)).fetchall()
        rek = conn.execute('SELECT * FROM rekeningen WHERE user_id=? ORDER BY code', (uid(),)).fetchall()

    if request.method == 'POST':
        return _factuur_opslaan(None)

    vandaag = datetime.now().strftime('%Y-%m-%d')
    nummer = volgend_factuurnummer(uid())
    return render_template('factuur_form.html',
                           factuur=None, regels=[], klanten=kl, rekeningen=rek,
                           nummer=nummer, vandaag=vandaag,
                           btw_opties=BTW_OPTIES, btw_label=btw_label)


@app.route('/facturen/<int:fid>/bewerken', methods=['GET', 'POST'])
@login_required
def facturen_bewerken(fid):
    with get_db() as conn:
        f = conn.execute('SELECT * FROM facturen WHERE id=? AND user_id=?', (fid, uid())).fetchone()
        if not f:
            flash('Factuur niet gevonden.', 'danger')
            return redirect(url_for('facturen'))
        if request.method == 'POST':
            return _factuur_opslaan(fid)
        regels = conn.execute('SELECT * FROM factuur_regels WHERE factuur_id=?', (fid,)).fetchall()
        kl = conn.execute('SELECT * FROM klanten WHERE user_id=? ORDER BY naam', (uid(),)).fetchall()
        rek = conn.execute('SELECT * FROM rekeningen WHERE user_id=? ORDER BY code', (uid(),)).fetchall()

    vandaag = datetime.now().strftime('%Y-%m-%d')
    return render_template('factuur_form.html',
                           factuur=f, regels=regels, klanten=kl, rekeningen=rek,
                           nummer=f['factuurnummer'], vandaag=vandaag,
                           btw_opties=BTW_OPTIES, btw_label=btw_label)


def _factuur_opslaan(fid):
    klant_id = request.form.get('klant_id') or None
    nummer = request.form['factuurnummer'].strip()
    datum = request.form['factuurdatum']
    profiel = int(request.form.get('betaalprofiel') or 30)
    vervaldatum = request.form.get('vervaldatum') or ''
    notitie = request.form.get('notitie', '')
    bedragen_excl = 1 if request.form.get('bedragen_excl') == '1' else 0
    status = request.form.get('status', 'concept')

    omschrijvingen = request.form.getlist('omschrijving[]')
    rekening_ids   = request.form.getlist('rekening_id[]')
    btw_waardes    = request.form.getlist('btw_waarde[]')
    aantallen      = request.form.getlist('aantal[]')
    prijzen        = request.form.getlist('eenheidsprijs[]')

    with get_db() as conn:
        if fid:
            conn.execute(
                'UPDATE facturen SET klant_id=?,factuurnummer=?,factuurdatum=?,vervaldatum=?,betaalprofiel=?,notitie=?,bedragen_excl=?,status=? WHERE id=? AND user_id=?',
                (klant_id, nummer, datum, vervaldatum, profiel, notitie, bedragen_excl, status, fid, uid())
            )
            conn.execute('DELETE FROM factuur_regels WHERE factuur_id=?', (fid,))
            factuur_id = fid
        else:
            factuur_id = conn.execute(
                'INSERT INTO facturen (user_id,klant_id,factuurnummer,factuurdatum,vervaldatum,betaalprofiel,notitie,bedragen_excl,status) VALUES (?,?,?,?,?,?,?,?,?)',
                (uid(), klant_id, nummer, datum, vervaldatum, profiel, notitie, bedragen_excl, status)
            ).lastrowid

        for i in range(len(omschrijvingen)):
            try:
                conn.execute(
                    'INSERT INTO factuur_regels (factuur_id,omschrijving,rekening_id,btw_waarde,aantal,eenheidsprijs) VALUES (?,?,?,?,?,?)',
                    (factuur_id, omschrijvingen[i],
                     rekening_ids[i] if rekening_ids[i] else None,
                     btw_waardes[i] if i < len(btw_waardes) else '21',
                     float(aantallen[i] or 1) if i < len(aantallen) else 1,
                     float(prijzen[i].replace(',', '.') or 0) if i < len(prijzen) else 0)
                )
            except Exception:
                pass

    label = 'Concept opgeslagen.' if status == 'concept' else 'Factuur vastgelegd.'
    flash(label, 'success')
    return redirect(url_for('facturen_bekijken', fid=factuur_id))


@app.route('/facturen/<int:fid>')
@login_required
def facturen_bekijken(fid):
    with get_db() as conn:
        f = conn.execute(
            'SELECT f.*, k.naam AS klant_naam, k.email AS klant_email, k.adres, k.postcode, k.stad, k.btw_nummer AS klant_btw, k.kvk_nummer AS klant_kvk '
            'FROM facturen f LEFT JOIN klanten k ON f.klant_id=k.id WHERE f.id=? AND f.user_id=?',
            (fid, uid())
        ).fetchone()
        if not f:
            flash('Factuur niet gevonden.', 'danger')
            return redirect(url_for('facturen'))
        regels = conn.execute('SELECT fr.*, r.naam AS rek_naam FROM factuur_regels fr LEFT JOIN rekeningen r ON fr.rekening_id=r.id WHERE fr.factuur_id=?', (fid,)).fetchall()

    excl, btw_per_code, totaal_btw, totaal_incl = bereken_factuur_totalen(regels, f['bedragen_excl'])
    return render_template('factuur_detail.html',
                           f=f, regels=regels, btw_label=btw_label,
                           excl=excl, btw_per_code=btw_per_code,
                           totaal_btw=totaal_btw, totaal_incl=totaal_incl,
                           btw_pct_van=btw_pct_van)


@app.route('/facturen/<int:fid>/status', methods=['POST'])
@login_required
def facturen_status(fid):
    nieuwe_status = request.form['status']
    with get_db() as conn:
        conn.execute('UPDATE facturen SET status=? WHERE id=? AND user_id=?', (nieuwe_status, fid, uid()))
    return redirect(request.referrer or url_for('facturen'))


@app.route('/facturen/<int:fid>/verwijder', methods=['POST'])
@login_required
def facturen_verwijder(fid):
    with get_db() as conn:
        conn.execute('DELETE FROM factuur_regels WHERE factuur_id=?', (fid,))
        conn.execute('DELETE FROM facturen WHERE id=? AND user_id=?', (fid, uid()))
    flash('Factuur verwijderd.', 'info')
    return redirect(url_for('facturen'))


if __name__ == '__main__':
    app.run(debug=True, port=5050, host='127.0.0.1')
