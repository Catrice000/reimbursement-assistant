"""Integration checks for publication, authorization, and facts/history consistency."""
import unittest,tempfile,threading,json,urllib.request,urllib.error,sys,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();server.DB=Path(self.tmp.name)/'app.db';server.SESSIONS.clear();server.LOGIN_ATTEMPTS.clear();server.initialize()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_port}'
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();self.tmp.cleanup()
    def req(self,path,data=None,auth=None):
        headers={'Content-Type':'application/json'}
        if auth:headers.update({'Cookie':auth[0],'X-CSRF-Token':auth[1]})
        request=urllib.request.Request(self.base+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(request) as r:return r.status,json.loads(r.read()),r.headers
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read()),e.headers
    def login(self,name):
        status,data,headers=self.req('login',{'username':name,'password':'demo1234'});self.assertEqual(status,200)
        return headers['Set-Cookie'].split(';')[0],data['csrf']
    def test_revocation_blocks_existing_session_and_preserves_policy_access(self):
        employee=self.login('employee');finance=self.login('finance')
        self.assertEqual(self.req('claims/R-0001',auth=employee)[0],200)
        self.assertEqual(self.req('access',{'employee':'employee','allowed':False},finance)[0],200)
        self.assertEqual(self.req('claims',auth=employee)[0],403)
        self.assertEqual(self.req('claims/R-0001',auth=employee)[0],403)
        self.assertEqual(self.req('search',{'question':'住宿'},employee)[0],200)
        server.initialize() # restart migration must not silently restore revoked permission
        self.assertEqual(self.req('claims',auth=employee)[0],403)
        self.assertEqual(self.req('access',{'employee':'employee','allowed':True},finance)[0],200)
        self.assertEqual(self.req('claims/R-0002',auth=employee)[0],404)
    def test_finance_history_and_stale_write(self):
        finance=self.login('finance');employee=self.login('employee')
        c=self.req('claims/R-0001',auth=finance)[1]
        data={'revision':c['revision'],'status':'待补充材料','note':'请补充发票'}
        self.assertEqual(self.req('claims/R-0001',data,finance)[0],200)
        self.assertEqual(self.req('claims/R-0001',{**data,'note':'覆盖'},finance)[0],409)
        current=self.req('claims/R-0001',auth=employee)[1]
        self.assertEqual(current['note'],'请补充发票');self.assertEqual(len(current['history']),2)
        self.assertEqual(current['history'][0]['actor_name'],'周敏')
        with server.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='核对单据'").fetchone()[0],1)
    def test_draft_edit_and_publish_lifecycle(self):
        admin=self.login('admin');employee=self.login('employee')
        data={'title':'新增交通制度（演示）','version':'1.0','start':'2026-01-01','end':'','scope':'全体员工','content':'第1条 地铁费用应提供行程凭证。'}
        pid=self.req('policies',data,admin)[1]['id']
        self.assertFalse(any(p['id']==pid for p in self.req('policies',auth=employee)[1]))
        data['content']='第1条 地铁费用应提供电子发票。'
        self.assertEqual(self.req(f'policies/{pid}',data,admin)[0],200)
        self.assertEqual(self.req(f'policies/{pid}/publish',{},admin)[0],200)
        self.assertEqual(self.req(f'policies/{pid}',data,admin)[0],409)
        self.assertTrue(any(s['policy_id']==pid for s in self.req('search',{'question':'地铁费用'},employee)[1]['sources']))
        self.assertEqual(self.req(f'policies/{pid}/withdraw',{},admin)[0],200)
        self.assertFalse(any(s['policy_id']==pid for s in self.req('search',{'question':'地铁费用'},employee)[1]['sources']))
    def test_role_permissions_and_input_validation(self):
        employee=self.login('employee');admin=self.login('admin');finance=self.login('finance')
        self.assertEqual(self.req('access',auth=employee)[0],403)
        self.assertEqual(self.req('access',{'employee':'employee','allowed':True},admin)[0],403)
        self.assertEqual(self.req('access',{'employee':'employee','allowed':'yes'},finance)[0],400)
        self.assertEqual(self.req('claims/R-0001',auth=admin)[0],403)
        self.assertEqual(self.req('policies',[],admin)[0],400)
        self.assertEqual(self.req('search',{'question':''},employee)[0],400)
        self.assertEqual(self.req('claims/R-0001',{'status':'已完成','note':'无版本'},finance)[0],400)
    def test_failed_login_limit(self):
        for _ in range(5):self.assertEqual(self.req('login',{'username':'employee','password':'wrong'})[0],401)
        self.assertEqual(self.req('login',{'username':'employee','password':'demo1234'})[0],429)
    def test_legacy_database_upgrade(self):
        legacy=Path(self.tmp.name)/'legacy.db'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE claims(id TEXT PRIMARY KEY,owner TEXT,title TEXT,amount REAL,status TEXT,note TEXT,updated TEXT,verified_by TEXT)')
            db.execute("INSERT INTO claims VALUES('OLD','employee','旧记录',10,'已完成','原始备注','2025-01-01','finance')")
        old_db=server.DB
        try:
            server.DB=legacy;server.initialize()
            with server.connect() as db:
                self.assertEqual(db.execute("SELECT note,revision FROM claims WHERE id='OLD'").fetchone()['note'],'原始备注')
                self.assertEqual(db.execute("SELECT count(*) FROM claim_history WHERE claim='OLD'").fetchone()[0],1)
        finally:server.DB=old_db

if __name__=='__main__':unittest.main()
