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

    try:
        gc = get_sheets_client()
        planilha = gc.open_by_key(ID_PLANILHA_OFICIOS)
        sheet = planilha.get_worksheet(0)
        all_rows = sheet.get_all_values()
        
        if len(all_rows) > 1:
            headers = [h.strip().upper() for h in all_rows[0]]
            for row in all_rows[1:]:
                # Mapeia cada coluna usando o header se existir, ou pega por índice
                row_data = dict(zip(headers, row))
                
                data_val  = str(row_data.get("DATA", row[0] if len(row) > 0 else "")).strip()
                numero    = str(row_data.get("NUMERO", row[1] if len(row) > 1 else "")).strip()
                remetente = str(row_data.get("REMETENTE", row[2] if len(row) > 2 else "")).strip()
                assunto   = str(row_data.get("ASSUNTO", row[3] if len(row) > 3 else "")).strip()
                link_pdf  = str(row_data.get("LINK_PDF", row[4] if len(row) > 4 else "")).strip()
                status    = str(row_data.get("STATUS", row[5] if len(row) > 5 else "Recebido")).strip()
                
                if not status:
                    status = "Recebido"

                if numero or assunto:
                    banco_oficios.append({
                        "data":      data_val,
                        "numero":    numero,
                        "remetente": remetente,
                        "assunto":   assunto,
                        "link_pdf":  link_pdf,
                        "status":    status
                    })
        print(f"✅ Total de {len(banco_oficios)} ofício(s) indexado(s) com sucesso via gspread!")
    except Exception as e:
        print(f"❌ Erro ao ler planilha de ofícios: {e}")

    return len(banco_oficios)

# ----------------------------------------------------------------------
# 3. ROTAS DA API
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# ----------------------------------------------------------------------
# PROXY DE PDF — serve o arquivo do Cloudinary com Content-Type correto
# ----------------------------------------------------------------------
@app.route("/api/pdf-proxy")
def pdf_proxy():
    from flask import Response
    import urllib.parse
    url = request.args.get("url", "").strip()
    if not url or "cloudinary.com" not in url:
        return "URL inválida", 400
    try:
        # Extrai o nome do arquivo da URL do Cloudinary
        path = urllib.parse.urlparse(url).path
        filename = path.split("/")[-1]
        filename = urllib.parse.unquote(filename)  # decodifica %20 etc.
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        r = requests.get(url, timeout=30)
        return Response(
            r.content,
            status=200,
            content_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "public, max-age=3600"
            }
        )
    except Exception as e:
        return f"Erro ao buscar PDF: {e}", 500

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
            public_id = f"oficios/{numero.replace('/', '-').replace(' ', '_')}_{timestamp}"

            print(f"📤 Enviando PDF para Cloudinary: {public_id}")
            resultado = cloudinary.uploader.upload(
                arquivo,
                resource_type = "raw",
                public_id     = public_id,
                overwrite     = True,
                use_filename  = False
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

# ----------------------------------------------------------------------
# 5. DELETAR OFÍCIO
# ----------------------------------------------------------------------
@app.route("/api/deletar-oficio", methods=["DELETE"])
def deletar_oficio():
    try:
        body      = request.get_json()
        numero    = (body.get("numero")    or "").strip().upper()
        remetente = (body.get("remetente") or "").strip().upper()
        assunto   = (body.get("assunto")   or "").strip()
        link_pdf  = (body.get("link_pdf")  or "").strip()

        if not numero and not assunto:
            return jsonify({"success": False, "error": "Dados insuficientes para identificar o ofício."}), 400

        # 1. Remove do Google Sheets (busca pela linha exata)
        gc       = get_sheets_client()
        planilha = gc.open_by_key(ID_PLANILHA_OFICIOS)
        sheet    = planilha.get_worksheet(0)
        all_rows = sheet.get_all_values()

        row_to_delete = None
        if len(all_rows) > 1:
            headers = [str(h).strip().upper() for h in all_rows[0]]
            # Determina os índices das colunas, caso existam, ou usa o padrão
            idx_num = headers.index("NUMERO") if "NUMERO" in headers else 1
            idx_rem = headers.index("REMETENTE") if "REMETENTE" in headers else 2
            
            for i, row in enumerate(all_rows[1:], start=2):  # pula cabeçalho
                row_numero    = str(row[idx_num]).strip().upper() if len(row) > idx_num else ""
                row_remetente = str(row[idx_rem]).strip().upper() if len(row) > idx_rem else ""
                
                if row_numero == numero and row_remetente == remetente:
                    row_to_delete = i
                    break

        if row_to_delete:
            sheet.delete_rows(row_to_delete)
            print(f"🗑️ Linha {row_to_delete} deletada do Sheets.")
        else:
            print(f"⚠️ Ofício não encontrado no Sheets para deletar. Possível inconsistência!")
            return jsonify({"success": False, "error": "O ofício já foi removido ou não existe na planilha."}), 404

        # 2. Remove o PDF do Cloudinary (se houver)
        if link_pdf:
            try:
                import re as _re
                # Extrai o public_id da URL do Cloudinary
                match = _re.search(r'/upload/(?:v\d+/)?(.+?)(?:\.\w+)?$', link_pdf)
                if match:
                    public_id = match.group(1)
                    # Garante extensão no public_id para resource_type=image
                    if not public_id.endswith('.pdf'):
                        public_id += '.pdf'
                    cloudinary.uploader.destroy(public_id, resource_type="image")
                    print(f"🗑️ PDF removido do Cloudinary: {public_id}")
            except Exception as e_cloud:
                print(f"⚠️ Não foi possível remover PDF do Cloudinary: {e_cloud}")

        # 3. Remove da memória
        global banco_oficios
        banco_oficios = [
            o for o in banco_oficios
            if not (o["numero"].upper() == numero and o["remetente"].upper() == remetente)
        ]

        return jsonify({"success": True, "message": "Ofício excluído com sucesso!"})

    except Exception as e:
        print(f"❌ Erro ao deletar ofício: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ----------------------------------------------------------------------
# 6. DELETAR TODOS OS OFÍCIOS
# ----------------------------------------------------------------------
@app.route("/api/deletar-todos", methods=["DELETE"])
def deletar_todos():
    try:
        # 1. Limpa Google Sheets (mantém só o cabeçalho)
        gc = get_sheets_client()
        planilha = gc.open_by_key(ID_PLANILHA_OFICIOS)
        sheet = planilha.get_worksheet(0)
        
        all_rows = sheet.get_all_values()
        if len(all_rows) > 1:
            # Salva o cabeçalho
            headers = all_rows[0]
            # Limpa toda a planilha de forma garantida
            sheet.clear()
            # Restaura o cabeçalho na primeira linha
            sheet.update(range_name='A1', values=[headers])
            print("🗑️ Planilha limpa e cabeçalhos restaurados.")
        
        # 2. Limpa Cloudinary (Opcional, tenta apagar os arquivos na pasta 'oficios')
        try:
            cloudinary.api.delete_resources_by_prefix("oficios/")
            print("🗑️ Arquivos da pasta 'oficios' no Cloudinary foram removidos.")
        except Exception as e_cloud:
            print(f"⚠️ Não foi possível limpar a pasta no Cloudinary: {e_cloud}")

        # 3. Limpa memória
        global banco_oficios
        banco_oficios = []

        return jsonify({"success": True, "message": "Todos os ofícios foram excluídos com sucesso!"})

    except Exception as e:
        print(f"❌ Erro ao deletar todos os ofícios: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# Carrega os ofícios ao iniciar (funciona com gunicorn e direto)
carregar_oficios()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 Sistema de Gestão de Ofícios SINTE-PI no ar na porta {port}!")
    print(f"👉 Acesse no navegador: http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)