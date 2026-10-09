"""Local demonstration app. All seeded records are fictional."""
import os, json, sqlite3, secrets, hashlib, hmac, re
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie

ROOT = Path(__file__).parent
DB = Path(os.environ.get('APP_DB', ROOT / 'data/app.db'))
SESSIONS = {}
STATUSES = ['材料审核中', '待补充材料', '待审批', '已完成', '已退回']

def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()

def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200000).hex()

def initialize():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT, role TEXT, salt TEXT, password TEXT);
        CREATE TABLE IF NOT EXISTS policies (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, version TEXT, start TEXT, end TEXT, scope TEXT, content TEXT, state TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, owner TEXT, title TEXT, amount REAL, status TEXT, note TEXT, updated TEXT, verified_by TEXT);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT, action TEXT, object TEXT, at TEXT);
        ''')
        if db.execute('SELECT count(*) FROM users').fetchone()[0] == 0:
            for uid, name, role in [('employee','小林','employee'),('employee2','小陈','employee'),('admin','乔姐','admin'),('finance','周敏','finance')]:
                salt = secrets.token_hex(16)
                db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (uid,name,role,salt,password_hash('demo1234',salt)))
            rows = [
                ('差旅报销管理制度（演示）','2026.1','2026-01-01','','全体员工','第1条 材料要求：出差报销应提交发票、行程单、出差审批记录。\n第2条 住宿标准：本演示制度住宿上限为每晚400元；超出部分须提供事前审批记录。\n第3条 提交期限：出差结束后30日内提交报销材料。','published',now()),
                ('日常费用报销制度（演示）','2026.1','2026-01-01','','全体员工','第1条 办公用品报销应提交发票、采购审批记录及费用用途说明。\n第2条 缺少材料时，由财务专员核对后记录补充要求。','published',now()),
                ('差旅报销管理制度（演示旧版）','2025.1','2025-01-01','2025-12-31','全体员工','第1条 旧版住宿上限为每晚300元。','published',now())]
            db.executemany('INSERT INTO policies(title,version,start,end,scope,content,state,updated) VALUES (?,?,?,?,?,?,?,?)',rows)
            db.executemany('INSERT INTO claims VALUES (?,?,?,?,?,?,?,?)',[
                ('R-0001','employee','杭州出差报销',1280,'材料审核中','财务已收到材料，待核对发票与行程单。',now(),'finance'),
                ('R-0002','employee2','办公用品报销',260,'待补充材料','请补充采购审批记录。',now(),'finance')])

def effective(policy, date=None):
    date = date or today()
    return policy['state']=='published' and policy['start'] <= date and (not policy['end'] or date <= policy['end'])

class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

def require_role(user, role):
    if user['role'] != role:
        raise Problem(403, '当前账号无权执行此操作')

def audit(db, user, action, obj):
    db.execute('INSERT INTO audit(actor,action,object,at) VALUES (?,?,?,?)',(user['id'],action,str(obj),now()))

def retrieve(db, question):
    """Model adapter slot: return grounded evidence; no model or external API invoked."""
    question = str(question).strip()
    if not question or len(question)>500:
        raise Problem(400,'请输入1至500字的问题')
    tokens = set(re.findall(r'[a-z0-9]+',question.lower()))
    tokens.update(question[i:i+2] for i in range(len(question)-1) if re.match(r'[\u4e00-\u9fff]{2}',question[i:i+2]))
    for term in ['出差','差旅','住宿','酒店','材料','发票','行程','办公','采购','期限','提交']:
        if term in question:
            tokens.add({'出差':'差旅','酒店':'住宿'}.get(term,term))
    results=[]
    for row in db.execute('SELECT * FROM policies'):
        p=dict(row)
        if not effective(p):
            continue
        for paragraph in p['content'].splitlines():
            score=sum(2 if t in paragraph else 1 if t in p['title'] else 0 for t in tokens)
            if score:
                results.append({'policy_id':p['id'],'title':p['title'],'version':p['version'],'start':p['start'],'scope':p['scope'],'excerpt':paragraph,'score':score})
    results.sort(key=lambda x:x['score'],reverse=True)
    return {'mode':'retrieval','message':'以下为当前有效制度的匹配原文，请核对适用范围。' if results else '未找到匹配的有效制度，请补充问题或联系行政专员。','sources':results[:5],'as_of':today()}

def list_claims(db,user):
    if user['role']=='finance':
        return [dict(r) for r in db.execute('SELECT * FROM claims ORDER BY id')]
    if user['role']=='employee':
        return [dict(r) for r in db.execute('SELECT * FROM claims WHERE owner=? ORDER BY id',(user['id'],))]
    raise Problem(403,'行政专员不具备单据查询权限')

def update_claim(db,user,claim_id,data):
    require_role(user,'finance')
    if data.get('status') not in STATUSES or not str(data.get('note','')).strip():
        raise Problem(400,'请选择状态并填写事实核对备注')
    result=db.execute('UPDATE claims SET status=?,note=?,updated=?,verified_by=? WHERE id=?',(data['status'],str(data['note'])[:2000],now(),user['id'],claim_id))
    if not result.rowcount:
        raise Problem(404,'单据不存在')
    audit(db,user,'核对单据',claim_id)

def save_policy(db,user,data):
    require_role(user,'admin')
    fields={k:str(data.get(k,'')).strip() for k in ['title','version','start','end','scope','content']}
    if any(not fields[k] for k in ['title','version','start','scope','content']):
        raise Problem(400,'请完整填写制度、版本、生效日期、适用范围及原文')
    try:
        datetime.strptime(fields['start'],'%Y-%m-%d')
        if fields['end']: datetime.strptime(fields['end'],'%Y-%m-%d')
    except ValueError:
        raise Problem(400,'日期格式应为YYYY-MM-DD')
    if fields['end'] and fields['end']<fields['start']:
        raise Problem(400,'失效日期不得早于生效日期')
    if len(fields['content'])>30000: raise Problem(400,'制度原文限30000字')
    # Revisions always create a draft; published records are never overwritten.
    cur=db.execute('INSERT INTO policies(title,version,start,end,scope,content,state,updated) VALUES (?,?,?,?,?,?,?,?)',(*fields.values(),'draft',now()))
    audit(db,user,'创建制度草稿',cur.lastrowid)
    return cur.lastrowid

class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200, cookie=None):
        payload=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(payload)
    def body(self):
        size=int(self.headers.get('Content-Length','0'))
        if size>100000:raise Problem(413,'请求过大')
        try:return json.loads(self.rfile.read(size) or b'{}')
        except (ValueError,UnicodeError):raise Problem(400,'无效JSON请求')
    def session(self):
        cookie=SimpleCookie(self.headers.get('Cookie',''))
        token=cookie.get('session')
        session=SESSIONS.get(token.value if token else '')
        if not session or session['expires']<datetime.now().timestamp():raise Problem(401,'请先登录')
        return session
    def do_GET(self):self.handle_request('GET')
    def do_POST(self):self.handle_request('POST')
    def handle_request(self,method):
        try:
            path=self.path.split('?')[0]
            if not path.startswith('/api/'):
                files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
                if method!='GET' or path not in files:raise Problem(404,'页面不存在')
                file=ROOT/'static'/files[path]
                self.send_response(200)
                self.send_header('Content-Type', {'html':'text/html','js':'text/javascript','css':'text/css'}[file.suffix[1:]]+'; charset=utf-8')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
                self.end_headers();self.wfile.write(file.read_bytes());return
            data=self.body() if method=='POST' else {}
            with connect() as db:
                if path=='/api/login' and method=='POST':
                    row=db.execute('SELECT * FROM users WHERE id=?',(data.get('username'),)).fetchone()
                    if not row or not hmac.compare_digest(row['password'],password_hash(str(data.get('password','')),row['salt'])):raise Problem(401,'账号或密码不正确')
                    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                    user={k:row[k] for k in ['id','name','role']}
                    SESSIONS[token]={'user':user,'csrf':csrf,'expires':datetime.now().timestamp()+28800}
                    self.send_json({'user':user,'csrf':csrf},cookie=f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800');return
                session=self.session();user=session['user']
                if method=='POST' and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):raise Problem(403,'请求校验失败，请重新登录')
                if path=='/api/me' and method=='GET':result={'user':user,'csrf':session['csrf']}
                elif path=='/api/logout' and method=='POST':
                    cookie=SimpleCookie(self.headers.get('Cookie',''));SESSIONS.pop(cookie['session'].value,None)
                    self.send_json({'ok':True},cookie='session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0');return
                elif path=='/api/policies' and method=='GET':
                    result=[]
                    for row in db.execute('SELECT * FROM policies ORDER BY id DESC'):
                        p=dict(row);p['effective']=effective(p)
                        if user['role']=='admin' or p['effective']:result.append(p)
                elif path=='/api/search' and method=='POST':result=retrieve(db,data.get('question',''))
                elif path=='/api/policies' and method=='POST':result={'id':save_policy(db,user,data)}
                elif re.fullmatch(r'/api/policies/\d+/(publish|withdraw)',path) and method=='POST':
                    require_role(user,'admin');pid=int(path.split('/')[3]);action=path.split('/')[4]
                    row=db.execute('SELECT * FROM policies WHERE id=?',(pid,)).fetchone()
                    if not row:raise Problem(404,'制度不存在')
                    if action=='publish':
                        if row['state']!='draft':raise Problem(400,'只能发布草稿')
                        # Prevent overlapping published versions of the same titled policy.
                        overlap=db.execute("SELECT id FROM policies WHERE title=? AND state='published' AND start<=? AND (end='' OR end>=?)",(row['title'],row['end'] or '9999-12-31',row['start'])).fetchone()
                        if overlap:raise Problem(409,'同名制度存在有效期重叠的已发布版本，请先撤回旧版或调整新版日期')
                    db.execute('UPDATE policies SET state=?,updated=? WHERE id=?',('published' if action=='publish' else 'withdrawn',now(),pid));audit(db,user,action,pid);result={'ok':True}
                elif path=='/api/claims' and method=='GET':result=list_claims(db,user)
                elif re.fullmatch(r'/api/claims/[A-Za-z0-9-]+',path) and method=='POST':update_claim(db,user,path.rsplit('/',1)[1],data);result={'ok':True}
                elif path=='/api/audit' and method=='GET':
                    if user['role'] not in ['admin','finance']:raise Problem(403,'无权查看操作记录')
                    result=[dict(r) for r in db.execute('SELECT * FROM audit WHERE actor=? ORDER BY id DESC LIMIT 50',(user['id'],))]
                else:raise Problem(404,'接口不存在')
                self.send_json(result)
        except Problem as exc:self.send_json({'error':exc.message},exc.status)
        except (ValueError,TypeError,KeyError):self.send_json({'error':'请求参数无效'},400)
        except Exception:
            self.send_json({'error':'服务暂时不可用'},500)
    def log_message(self,fmt,*args):pass

if __name__=='__main__':
    initialize()
    port=int(os.environ.get('PORT','8000'))
    print(f'财务报销助手演示版：http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
