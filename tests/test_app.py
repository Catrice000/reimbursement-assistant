import unittest,tempfile,threading,json,urllib.request,urllib.error,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
class AppTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();server.DB=Path(self.tmp.name)/'test.db';server.initialize()
  self.employee={'id':'employee','role':'employee'};self.admin={'id':'admin','role':'admin'};self.finance={'id':'finance','role':'finance'}
 def tearDown(self):self.tmp.cleanup()
 def test_effective_boundaries(self):
  p={'state':'published','start':'2026-01-01','end':'2026-12-31'}
  self.assertTrue(server.effective(p,'2026-01-01'));self.assertTrue(server.effective(p,'2026-12-31'));self.assertFalse(server.effective(p,'2027-01-01'))
  p['state']='withdrawn';self.assertFalse(server.effective(p,'2026-03-01'))
 def test_current_sources_and_unknown(self):
  with server.connect() as db:
   result=server.retrieve(db,'住宿标准')['sources'];self.assertTrue(any('400' in s['excerpt'] for s in result));self.assertFalse(any('300元' in s['excerpt'] for s in result));self.assertEqual(server.retrieve(db,'量子计算')['sources'],[])
   db.execute("UPDATE policies SET state='withdrawn' WHERE id=1");self.assertEqual(server.retrieve(db,'住宿标准')['sources'],[])
 def test_ownership_and_roles(self):
  with server.connect() as db:
   self.assertEqual([c['id'] for c in server.list_claims(db,self.employee)],['R-0001']);self.assertEqual(len(server.list_claims(db,self.finance)),2)
   with self.assertRaises(server.Problem):server.list_claims(db,self.admin)
   with self.assertRaises(server.Problem):server.update_claim(db,self.employee,'R-0002',{'status':'已完成','note':'假的'})
   server.update_claim(db,self.finance,'R-0001',{'status':'待补充材料','note':'缺发票'});self.assertEqual(server.list_claims(db,self.employee)[0]['note'],'缺发票');self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],1)
 def test_revision(self):
  with server.connect() as db:
   data=dict(db.execute('SELECT * FROM policies WHERE id=1').fetchone());data['version']='2026.2'
   with self.assertRaises(server.Problem):server.save_policy(db,self.finance,data)
   pid=server.save_policy(db,self.admin,data);self.assertEqual(db.execute('SELECT state FROM policies WHERE id=?',(pid,)).fetchone()[0],'draft');self.assertEqual(db.execute('SELECT version FROM policies WHERE id=1').fetchone()[0],'2026.1')
 def test_http_boundaries(self):
  http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{http.server_port}'
  def req(path,data=None,cookie='',csrf=''):
   request=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','Cookie':cookie,'X-CSRF-Token':csrf})
   try:
    with urllib.request.urlopen(request) as r:return r.status,json.loads(r.read()),r.headers
   except urllib.error.HTTPError as e:return e.code,json.loads(e.read()),e.headers
  try:
   self.assertEqual(req('/api/claims')[0],401)
   status,d,h=req('/api/login',{'username':'employee','password':'demo1234'});self.assertEqual(status,200);cookie=h['Set-Cookie'].split(';')[0];csrf=d['csrf']
   self.assertEqual([c['id'] for c in req('/api/claims',cookie=cookie)[1]],['R-0001'])
   self.assertEqual(req('/api/claims/R-0002',{'status':'已完成','note':'越权'},cookie,csrf)[0],403)
   self.assertEqual(req('/api/search',{'question':'材料'},cookie)[0],403);self.assertEqual(req('/api/search',{'question':'材料'},cookie,csrf)[0],200)
   _,d,h=req('/api/login',{'username':'admin','password':'demo1234'});cookie=h['Set-Cookie'].split(';')[0];csrf=d['csrf']
   data={'title':'差旅报销管理制度（演示）','version':'2026.2','start':'2026-01-01','end':'','scope':'全体员工','content':'第1条 新版本'}
   _,d,_=req('/api/policies',data,cookie,csrf);pid=d['id'];self.assertEqual(req(f'/api/policies/{pid}/publish',{},cookie,csrf)[0],409)
   self.assertEqual(req('/api/policies/1/withdraw',{},cookie,csrf)[0],200);self.assertEqual(req(f'/api/policies/{pid}/publish',{},cookie,csrf)[0],200);self.assertEqual(req('/api/claims',cookie=cookie)[0],403)
  finally:http.shutdown();http.server_close();thread.join()
if __name__=='__main__':unittest.main()
