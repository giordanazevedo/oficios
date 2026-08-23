import os
import io
import json
import tempfile
import datetime
import pandas as pd
import requests
# pyrefly: ignore [missing-import]
import cloudinary
import cloudinary.uploader
from flask import Flask, jsonify, request, send_from_directory
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__, static_folder=".")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ======================================================================
# CONFIGURAÇÕES DO CLOUDINARY (armazenamento de PDFs na nuvem)
# ======================================================================
# Se CLOUDINARY_URL estiver definida (ex: Railway), o SDK lê automaticamente.
# Caso contrário, configura com variáveis individuais ou valores padrão.
if not os.environ.get("CLOUDINARY_URL"):
    cloudinary.config(
        cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "a2t90mzc"),
        api_key    = os.environ.get("CLOUDINARY_API_KEY",    "998998274244915"),
        api_secret = os.environ.get("CLOUDINARY_API_SECRET", "4prlbU1YG9ecfm40sX6_5tY49K0"),
        secure     = True
)
cloudinary.config(secure=True)

# ======================================================================
# CONFIGURAÇÕES DO GOOGLE SHEETS
# ======================================================================
ID_PLANILHA_OFICIOS = "1XrFpPOrAFip48Wxun0_tnc2GjiIp06nZmMGFeh-0NiI"
CREDENTIALS_FILE = "credentials.json"

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# ======================================================================
# CARREGA CREDENCIAIS DO GOOGLE (variável de ambiente ou arquivo local)
# ======================================================================
_GOOGLE_CREDS_ENV = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
_temp_creds_file = None

if _GOOGLE_CREDS_ENV:
    # Modo nuvem: cria arquivo temporário a partir da variável de ambiente
    _temp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    _temp.write(_GOOGLE_CREDS_ENV)
    _temp.flush()
    _temp.close()
    CREDENTIALS_FILE = _temp.name
    _temp_creds_file = _temp.name
    print("✅ Credenciais Google carregadas via variável de ambiente.")
elif os.path.exists(CREDENTIALS_FILE):
    print("✅ Credenciais Google carregadas do arquivo local.")
else:
    print("⚠️  AVISO: Nenhuma credencial Google encontrada! Configure GOOGLE_CREDENTIALS_JSON.")

banco_oficios = []

def get_sheets_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
    return gspread.authorize(creds)

# Rota para servir arquivos locais antigos (compatibilidade)
@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ----------------------------------------------------------------------
# 1. HEALTH CHECK
# ----------------------------------------------------------------------
@app.route('/health')
def health():
    return jsonify({"status": "ok", "oficios_em_memoria": len(banco_oficios)})

# ----------------------------------------------------------------------
# 2. FUNÇÃO PARA CARREGAR OS OFÍCIOS EM MEMÓRIA
# ----------------------------------------------------------------------
def carregar_oficios():
    global banco_oficios
    banco_oficios = []
    print("\n🔄 Carregando base de ofícios do Google Sheets...")

    url_xlsx = f"https://docs.google.com/spreadsheets/d/{ID_PLANILHA_OFICIOS}/export?format=xlsx"
    try:
        res = requests.get(url_xlsx)
        if res.status_code == 200:
            df = pd.read_excel(io.BytesIO(res.content))
            df.columns = [str(c).strip().upper() for c in df.columns]

            for _, linha in df.iterrows():
                data_val = str(linha.get("DATA", "")).strip()
                if " 00:00:00" in data_val:
                    data_val = data_val.replace(" 00:00:00", "")

                numero    = str(linha.get("NUMERO", "")).strip()
                remetente = str(linha.get("REMETENTE", "")).strip()
                assunto   = str(linha.get("ASSUNTO", "")).strip()
                link_pdf  = str(linha.get("LINK_PDF", "")).strip()
                status    = str(linha.get("STATUS", "Recebido")).strip()

                if (numero and numero.upper() != "NAN") or (assunto and assunto.upper() != "NAN"):
                    banco_oficios.append({
                        "data":      data_val  if data_val.upper()  != "NAN" else "",
                        "numero":    numero    if numero.upper()    != "NAN" else "",
                        "remetente": remetente if remetente.upper() != "NAN" else "",
                        "assunto":   assunto   if assunto.upper()   != "NAN" else "",
                        "link_pdf":  link_pdf  if link_pdf.upper()  != "NAN" else "",
                        "status":    status    if status.upper()    != "NAN" else "Recebido"
                    })
            print(f"✅ Total de {len(banco_oficios)} ofício(s) indexado(s) com sucesso!")
        else:
            print(f"⚠️ Google Sheets retornou status {res.status_code}")
    except Exception as e:
        print(f"❌ Erro ao ler planilha de ofícios: {e}")

    return len(banco_oficios)

# ----------------------------------------------------------------------
# 3. ROTAS DA API
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/oficios")
def listar_oficios():
    q     = request.args.get("q",     "").strip().upper()
    orgao = request.args.get("orgao", "").strip().upper()
    mes   = request.args.get("mes",   "").strip()
    ano   = request.args.get("ano",   "").strip()

    filtrados = []

    for item in banco_oficios:
        if q:
            match_texto = (
                q in item["numero"].upper() or
                q in item["remetente"].upper() or
                q in item["assunto"].upper()
            )
            if not match_texto:
                continue

        if orgao and orgao != "TODOS":
            if orgao not in item["remetente"].upper():
                continue

        data_str = item["data"]
        if mes or ano:
            partes = data_str.replace("-", "/").split("/")
            if len(partes) == 3:
                d_ano = partes[2] if len(partes[2]) == 4 else partes[0]
                d_mes = partes[1]

                if ano and d_ano != ano:
                    continue
                if mes and d_mes.zfill(2) != mes.zfill(2):
                    continue

        filtrados.append(item)

    return jsonify({"results": filtrados, "total": len(filtrados)})

@app.route("/api/orgaos")
def listar_orgaos():
    orgaos = set(item["remetente"] for item in banco_oficios if item["remetente"])
    return jsonify({"orgaos": sorted(list(orgaos))})

@app.route("/api/recarregar", methods=["POST"])
def recarregar():
    """Força recarregamento dos ofícios a partir do Google Sheets."""
    total = carregar_oficios()
    return jsonify({"success": True, "total": total, "message": f"{total} ofício(s) carregado(s) do Sheets."})

@app.route("/api/cadastrar-oficio", methods=["POST"])
def cadastrar_oficio():
    try:
        numero       = request.form.get("numero",   "").strip().upper()
        remetente    = request.form.get("remetente","").strip().upper()
        data_oficio  = request.form.get("data",     "").strip()
        assunto      = request.form.get("assunto",  "").strip()
        status       = request.form.get("status",   "Recebido").strip()
        arquivo      = request.files.get("arquivo")

        if not numero or not remetente or not assunto:
            return jsonify({"success": False, "error": "Número, Remetente e Assunto são obrigatórios."}), 400

        link_pdf = ""

        # 1. Envia o PDF para o Cloudinary (nuvem gratuita — link permanente)
        if arquivo and arquivo.filename:
            import unicodedata, re
            nome_seguro = unicodedata.normalize('NFKD', arquivo.filename)
            nome_seguro = nome_seguro.encode('ascii', 'ignore').decode('ascii')
            nome_seguro = re.sub(r'[^\w\-_\. ]', '_', nome_seguro).strip()

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            public_id = f"oficios/{numero.replace('/', '-')}_{timestamp}.pdf"

            print(f"📤 Enviando PDF para Cloudinary: {public_id}")
            resultado = cloudinary.uploader.upload(
                arquivo,
                resource_type = "auto",
                public_id     = public_id,
                overwrite     = True,
                use_filename  = False,
                format        = "pdf"
            )
            link_pdf = resultado["secure_url"]
            print(f"✅ PDF na nuvem: {link_pdf}")

        # 2. Formata a data
        if "-" in data_oficio and len(data_oficio.split("-")) == 3:
            ano_f, mes_f, dia_f = data_oficio.split("-")
            data_oficio = f"{dia_f}/{mes_f}/{ano_f}"

        # 3. Salva na Planilha Google Sheets
        gc       = get_sheets_client()
        planilha = gc.open_by_key(ID_PLANILHA_OFICIOS)
        sheet    = planilha.get_worksheet(0)

        nova_linha = [data_oficio, numero, remetente, assunto, link_pdf, status]
        sheet.append_row(nova_linha)

        # 4. Adiciona em memória para consulta instantânea
        novo_registro = {
            "data":      data_oficio,
            "numero":    numero,
            "remetente": remetente,
            "assunto":   assunto,
            "link_pdf":  link_pdf,
            "status":    status
        }
        banco_oficios.insert(0, novo_registro)

        return jsonify({"success": True, "message": "Ofício cadastrado com sucesso!", "link_pdf": link_pdf})

    except Exception as e:
        print(f"❌ Erro ao cadastrar ofício: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# Carrega os ofícios ao iniciar (funciona com gunicorn e direto)
carregar_oficios()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 Sistema de Gestão de Ofícios SINTE-PI no ar na porta {port}!")
    print(f"👉 Acesse no navegador: http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)