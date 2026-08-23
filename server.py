import os
import io
import json
import tempfile
import datetime
import requests
# pyrefly: ignore [missing-import]
import cloudinary
import cloudinary.uploader
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder=".")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ======================================================================
# CONFIGURAÇÕES DO CLOUDINARY (armazenamento de PDFs na nuvem)
# ======================================================================
if not os.environ.get("CLOUDINARY_URL"):
    cloudinary.config(
        cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "a2t90mzc"),
        api_key    = os.environ.get("CLOUDINARY_API_KEY",    "998998274244915"),
        api_secret = os.environ.get("CLOUDINARY_API_SECRET", "4prlbU1YG9ecfm40sX6_5tY49K0"),
        secure     = True
    )
cloudinary.config(secure=True)

# Rota para servir arquivos locais antigos (compatibilidade)
@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ----------------------------------------------------------------------
# 2. CONEXÃO AO BANCO DE DADOS
# ----------------------------------------------------------------------
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Erro ao conectar ao PostgreSQL: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS oficios (
                        id SERIAL PRIMARY KEY,
                        data VARCHAR(50),
                        numero VARCHAR(100),
                        remetente VARCHAR(255),
                        assunto TEXT,
                        link_pdf TEXT,
                        status VARCHAR(50) DEFAULT 'Recebido',
                        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()
                print("✅ Tabela 'oficios' verificada/criada com sucesso no PostgreSQL.")
        except Exception as e:
            print(f"❌ Erro ao criar tabela: {e}")
        finally:
            conn.close()

init_db()

# ----------------------------------------------------------------------
# 3. ROTAS DA API
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

# ----------------------------------------------------------------------
# PROXY DE PDF — serve o arquivo do Cloudinary com Content-Type correto
# ----------------------------------------------------------------------
@app.route("/api/pdf-proxy")
def pdf_proxy():
    url = request.args.get("url", "").strip()
    if not url or "cloudinary.com" not in url:
        return "URL inválida", 400
    try:
        path = urllib.parse.urlparse(url).path
        filename = path.split("/")[-1]
        filename = urllib.parse.unquote(filename)
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

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Erro de conexão com o banco de dados"}), 500

    try:
        query = "SELECT * FROM oficios WHERE 1=1"
        params = []

        if q:
            query += " AND (UPPER(numero) LIKE %s OR UPPER(remetente) LIKE %s OR UPPER(assunto) LIKE %s)"
            like_q = f"%{q}%"
            params.extend([like_q, like_q, like_q])
            
        if orgao and orgao != "TODOS":
            query += " AND UPPER(remetente) LIKE %s"
            params.append(f"%{orgao}%")

        if ano:
            query += " AND data LIKE %s"
            params.append(f"%{ano}")
            
        if mes:
            query += " AND (data LIKE %s OR data LIKE %s)"
            params.extend([f"%/{mes.zfill(2)}/%", f"%-{mes.zfill(2)}-%"])

        query += " ORDER BY id DESC"

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            
        return jsonify({"results": rows, "total": len(rows)})
    except Exception as e:
        print(f"❌ Erro ao buscar ofícios: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/orgaos")
def listar_orgaos():
    conn = get_db_connection()
    if not conn:
        return jsonify({"orgaos": []})
        
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT UPPER(remetente) FROM oficios WHERE remetente IS NOT NULL AND remetente != ''")
            rows = cur.fetchall()
            orgaos = sorted([row[0] for row in rows])
        return jsonify({"orgaos": orgaos})
    except Exception as e:
        return jsonify({"orgaos": []})
    finally:
        conn.close()

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

        if arquivo and arquivo.filename:
            nome_seguro = unicodedata.normalize('NFKD', arquivo.filename)
            nome_seguro = nome_seguro.encode('ascii', 'ignore').decode('ascii')
            nome_seguro = re.sub(r'[^\w\-_\. ]', '_', nome_seguro).strip()

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            public_id = f"oficios/{numero.replace('/', '-').replace(' ', '_')}_{timestamp}"

            resultado = cloudinary.uploader.upload(
                arquivo,
                resource_type = "raw",
                public_id     = public_id,
                overwrite     = True,
                use_filename  = False
            )
            link_pdf = resultado["secure_url"]

        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Erro de conexão com o banco de dados."}), 500

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oficios (data, numero, remetente, assunto, link_pdf, status)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (data_oficio, numero, remetente, assunto, link_pdf, status))
            conn.commit()

        return jsonify({"success": True})

    except Exception as e:
        print(f"❌ Erro no cadastro: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if 'conn' in locals() and conn:
            conn.close()

@app.route("/api/deletar-oficio", methods=["DELETE"])
def deletar_oficio():
    try:
        body      = request.get_json()
        oficio_id = body.get("id")
        link_pdf  = (body.get("link_pdf")  or "").strip()

        if not oficio_id:
            return jsonify({"success": False, "error": "ID não fornecido para exclusão."}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Erro de conexão com o banco de dados."}), 500

        with conn.cursor() as cur:
            cur.execute("DELETE FROM oficios WHERE id = %s", (oficio_id,))
            conn.commit()

        if link_pdf:
            try:
                if "cloudinary.com" in link_pdf:
                    public_id_com_extensao = link_pdf.split("/upload/")[-1].split("/", 1)[-1]
                    version_index = public_id_com_extensao.find("v")
                    if version_index == 0:
                        public_id_com_extensao = public_id_com_extensao.split("/", 1)[-1]

                    cloudinary.uploader.destroy(public_id_com_extensao, resource_type="raw")
            except Exception as e_cloud:
                print(f"⚠️ Erro ao deletar no Cloudinary: {e_cloud}")

        return jsonify({"success": True})

    except Exception as e:
        print(f"❌ Erro ao deletar ofício: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if 'conn' in locals() and conn:
            conn.close()

@app.route("/api/deletar-todos", methods=["DELETE"])
def deletar_todos():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Erro de conexão com o banco de dados."}), 500

        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE oficios RESTART IDENTITY")
            conn.commit()

        try:
            cloudinary.api.delete_resources_by_prefix("oficios/")
        except Exception as e_cloud:
            print(f"⚠️ Não foi possível limpar a pasta no Cloudinary: {e_cloud}")

        return jsonify({"success": True, "message": "Todos os ofícios foram excluídos com sucesso!"})

    except Exception as e:
        print(f"❌ Erro ao deletar todos os ofícios: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 Sistema de Gestão de Ofícios SINTE-PI no ar na porta {port}!")
    app.run(host="0.0.0.0", port=port, debug=False)