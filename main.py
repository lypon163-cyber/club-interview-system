from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, StreamingResponse
import sqlite3
import uuid
import os
import urllib.request
import io
import csv
import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from dotenv import load_dotenv
from openai import OpenAI

# PDF 排版工具
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 加载环境变量
load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if DEEPSEEK_API_KEY:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    print("✅ DeepSeek API 初始化成功！")
else:
    client = None
    print("⚠️ 警告：未找到 DEEPSEEK_API_KEY，网页正常运行，AI 功能不可用。")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 自动下载中文字体
FONT_PATH = "SimHei.ttf"
if not os.path.exists(FONT_PATH):
    font_url = "https://raw.githubusercontent.com/StellarCN/scp_zh/master/fonts/SimHei.ttf"
    urllib.request.urlretrieve(font_url, FONT_PATH)
pdfmetrics.registerFont(TTFont('SimHei', FONT_PATH))

# 🌟 核心修改：所有连接加上 timeout=15，防止高并发锁死
def get_db():
    return sqlite3.connect('interview.db', timeout=15)

def init_db():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE, password_hash TEXT, role TEXT DEFAULT 'applicant')''')
        c.execute('''CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, barcode_id TEXT UNIQUE, username TEXT UNIQUE,
                name TEXT, student_id TEXT UNIQUE, phone_last4 TEXT, department TEXT, ps_content TEXT)''')
        
        c.execute("PRAGMA table_info(applications)")
        columns = [column[1] for column in c.fetchall()]
        if 'status' not in columns:
            c.execute("ALTER TABLE applications ADD COLUMN status TEXT DEFAULT '待审核'")
        if 'ai_summary' not in columns:
            c.execute("ALTER TABLE applications ADD COLUMN ai_summary TEXT DEFAULT ''")
            
        c.execute("SELECT * FROM users WHERE username = 'gjyyadmin'")
        if not c.fetchone():
            c.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", 
                      ('gjyyadmin', pwd_context.hash('20051212'), 'admin'))
        conn.commit()
    finally:
        conn.close()

init_db()

class UserAuth(BaseModel):
    username: str
    password: str
class ApplicationForm(BaseModel):
    username: str
    name: str
    student_id: str
    phone_last4: str
    department: str
    ps_content: str
class StatusUpdate(BaseModel):
    status: str

def generate_pdf(app_data):
    barcode_id, name, student_id, phone, dept, ps = app_data[:6]
    pdf_filename = f"{barcode_id}.pdf"
    barcode_path = f"{barcode_id}_b"
    qr_path = f"{barcode_id}_q.png"
    Code128(barcode_id, writer=ImageWriter()).save(barcode_path)
    qr = qrcode.make(barcode_id)
    qr.save(qr_path)

    doc = SimpleDocTemplate(pdf_filename, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
    elements = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', fontName='SimHei', fontSize=22, alignment=1, spaceAfter=20)
    text_style = ParagraphStyle('Text', fontName='SimHei', fontSize=11, leading=18)

    elements.append(Paragraph("柑橘音乐社团招新报名表", title_style))
    
    img_barcode = Image(f"{barcode_path}.png", width=160, height=50)
    img_qr = Image(qr_path, width=60, height=60)
    header_data = [[img_qr, img_barcode]]
    header_table = Table(header_data, colWidths=[60, 160])
    header_table.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'RIGHT')]))
    elements.append(header_table)
    elements.append(Spacer(1, 10))

    ps_html = ps.replace('\n', '<br/>')
    data = [
        ['专属编号', barcode_id, '意向部门', dept],
        ['姓名', name, '学号', student_id],
        ['手机尾号', phone, '状态', '已提交申请'],
        ['个人陈述', Paragraph(ps_html, text_style), '', '']
    ]
    table = Table(data, colWidths=[80, 150, 80, 185])
    table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'SimHei'), ('FONTSIZE', (0,0), (-1,-1), 11),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F5F5F5')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#F5F5F5')),
        ('SPAN', (1,3), (3,3)),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 30))
    footer_style = ParagraphStyle('Footer', fontName='SimHei', fontSize=10, textColor=colors.gray, alignment=1)
    elements.append(Paragraph("此表由系统自动生成，面试时请凭此表或专属编号签到。", footer_style))

    doc.build(elements)
    os.remove(f"{barcode_path}.png")
    os.remove(qr_path)
    return pdf_filename

@app.get("/", response_class=HTMLResponse)
def read_index():
    with open("apply.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/register")
def register(user: UserAuth):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (user.username, pwd_context.hash(user.password)))
        conn.commit()
        return {"status": "success", "message": "注册成功！"}
    except sqlite3.IntegrityError:
        return {"status": "error", "message": "用户名已被占用！"}
    finally:
        conn.close()

@app.post("/api/login")
def login(user: UserAuth):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT password_hash, role FROM users WHERE username = ?", (user.username,))
        res = c.fetchone()
        if res and pwd_context.verify(user.password, res[0]):
            return {"status": "success", "role": res[1], "username": user.username}
        return {"status": "error", "message": "账号或密码错误！"}
    finally:
        conn.close()

@app.post("/api/submit")
def submit_form(form: ApplicationForm):
    barcode_id = "TG" + uuid.uuid4().hex[:8].upper()
    
    # 🌟 在写数据库时使用 try...finally 确保一定关闭
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO applications (barcode_id, username, name, student_id, phone_last4, department, ps_content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (barcode_id, form.username, form.name, form.student_id, form.phone_last4, form.department, form.ps_content))
        conn.commit()
    except sqlite3.IntegrityError:
        return {"status": "error", "message": "你或该学号已经提交过报名表啦！"}
    except Exception as e:
        return {"status": "error", "message": f"报错: {str(e)}"}
    finally:
        conn.close()

    # 数据保存成功后再生成 PDF，绝不占用数据库
    try:
        pdf_file = generate_pdf((barcode_id, form.name, form.student_id, form.phone_last4, form.department, form.ps_content))
        with open(pdf_file, "rb") as f:
            pdf_bytes = f.read()
        os.remove(pdf_file)
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={barcode_id}.pdf"})
    except Exception as e:
        return {"status": "error", "message": f"PDF生成失败: {str(e)}"}

@app.get("/api/my_application")
def get_my_app(username: str):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT barcode_id, name, student_id, department, status FROM applications WHERE username = ?", (username,))
        res = c.fetchone()
        if res: return {"status": "success", "data": {"barcode_id": res[0], "name": res[1], "student_id": res[2], "dept": res[3], "status": res[4]}}
        return {"status": "error", "message": "暂无记录"}
    finally:
        conn.close()

@app.get("/api/admin/list")
def admin_list():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT barcode_id, name, student_id, department, phone_last4, status FROM applications")
        rows = c.fetchall()
        return {"status": "success", "data": rows}
    finally:
        conn.close()

@app.get("/api/admin/download/{barcode_id}")
def admin_download(barcode_id: str):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT barcode_id, name, student_id, phone_last4, department, ps_content FROM applications WHERE barcode_id = ?", (barcode_id,))
        res = c.fetchone()
    finally:
        conn.close()

    if res:
        pdf_file = generate_pdf(res)
        with open(pdf_file, "rb") as f:
            pdf_bytes = f.read()
        os.remove(pdf_file)
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f"inline; filename={barcode_id}.pdf"})
    raise HTTPException(status_code=404, detail="Not found")

@app.get("/api/admin/detail/{barcode_id}")
def admin_detail(barcode_id: str):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT name, department, ps_content, ai_summary, status FROM applications WHERE barcode_id = ?", (barcode_id,))
        res = c.fetchone()
    finally:
        conn.close()
        
    if res:
        return {"status": "success", "data": {"name": res[0], "dept": res[1], "ps": res[2], "ai_summary": res[3], "status": res[4]}}
    return {"status": "error", "message": "找不到记录"}

@app.post("/api/admin/ai_summarize/{barcode_id}")
def ai_summarize(barcode_id: str):
    if not client:
        return {"status": "error", "message": "尚未配置 DEEPSEEK_API_KEY，请检查 .env 文件或环境变量！"}

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT ps_content FROM applications WHERE barcode_id = ?", (barcode_id,))
        res = c.fetchone()
        if not res or not res[0]:
            return {"status": "error", "message": "该用户没有填写个人陈述"}
        ps_content = res[0]
        
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个社团招新负责人。请阅读以下申请人的个人陈述，提炼出该申请人的核心优势、技能或特点。要求：用一句话（不超过50字）总结，直接输出结果，不要任何客套话。"},
                {"role": "user", "content": ps_content}
            ],
            temperature=0.3,
            max_tokens=100
        )
        summary = response.choices[0].message.content.strip()
        
        c.execute("UPDATE applications SET ai_summary = ? WHERE barcode_id = ?", (summary, barcode_id))
        conn.commit()
        return {"status": "success", "summary": summary}
    except Exception as e:
        return {"status": "error", "message": f"AI 接口请求失败: {str(e)}"}
    finally:
        conn.close()

@app.post("/api/admin/update_status/{barcode_id}")
def update_status(barcode_id: str, data: StatusUpdate):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE applications SET status = ? WHERE barcode_id = ?", (data.status, barcode_id))
        conn.commit()
        return {"status": "success", "message": "状态已更新"}
    finally:
        conn.close()

@app.get("/api/admin/export")
def admin_export():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT barcode_id, name, student_id, phone_last4, department, ps_content, status FROM applications")
        rows = c.fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["专属编号", "姓名", "学号", "手机尾号", "意向部门", "个人陈述", "当前状态"])
    writer.writerows(rows)
    
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=applicants.csv"
    return response