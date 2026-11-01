import os,time,logging
from contextlib import asynccontextmanager
import psycopg
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel,Field
from prometheus_client import Counter,Histogram,generate_latest,CONTENT_TYPE_LATEST
from starlette.responses import Response
logging.basicConfig(level='INFO'); log=logging.getLogger('shop')
DB=os.getenv('DATABASE_URL','postgresql://shop:shop@localhost:5432/shop'); REQ=Counter('shop_http_requests_total','Requests',['method','path','status']); LAT=Histogram('shop_http_request_duration_seconds','Latency',['path'])
def db(): return psycopg.connect(DB)
def init():
 for i in range(30):
  try:
   with db() as c:
    c.execute('CREATE TABLE IF NOT EXISTS products(id SERIAL PRIMARY KEY,name TEXT,description TEXT,price_cents INT CHECK(price_cents>=0),stock INT CHECK(stock>=0))')
    c.execute('CREATE TABLE IF NOT EXISTS orders(id SERIAL PRIMARY KEY,product_id INT REFERENCES products(id),quantity INT,total_cents INT,created_at TIMESTAMPTZ DEFAULT now())')
    if c.execute('SELECT count(*) FROM products').fetchone()[0]==0: c.executemany('INSERT INTO products(name,description,price_cents,stock) VALUES(%s,%s,%s,%s)', [('GitOps Hoodie','Soft cotton hoodie',3999,25),('Kubernetes Mug','Durable operations mug',1499,50),('Argo CD Stickers','GitOps sticker pack',599,100)])
   return
  except Exception as e: log.warning('DB not ready: %s',e); time.sleep(2)
 raise RuntimeError('database unavailable')
@asynccontextmanager
async def life(app): init(); yield
app=FastAPI(title='Cloud Shop API',version=os.getenv('APP_VERSION','1.0.0'),lifespan=life)
class Order(BaseModel): product_id:int; quantity:int=Field(gt=0,le=20)
@app.middleware('http')
async def mw(req,call):
 t=time.time(); r=await call(req); REQ.labels(req.method,req.url.path,r.status_code).inc(); LAT.labels(req.url.path).observe(time.time()-t); return r
@app.get('/healthz')
def health(): return {'status':'ok','version':app.version}
@app.get('/readyz')
def ready():
 try:
  with db() as c:c.execute('SELECT 1')
  return {'status':'ready'}
 except Exception: raise HTTPException(503,'database not ready')
@app.get('/api/products')
def products():
 with db() as c: rows=c.execute('SELECT id,name,description,price_cents,stock FROM products ORDER BY id').fetchall()
 return [dict(zip(['id','name','description','price_cents','stock'],r)) for r in rows]
@app.post('/api/orders',status_code=201)
def order(o:Order):
 with db() as c:
  r=c.execute('SELECT price_cents,stock FROM products WHERE id=%s FOR UPDATE',(o.product_id,)).fetchone()
  if not r: raise HTTPException(404,'product not found')
  if r[1]<o.quantity: raise HTTPException(409,'insufficient stock')
  total=r[0]*o.quantity; left=c.execute('UPDATE products SET stock=stock-%s WHERE id=%s RETURNING stock',(o.quantity,o.product_id)).fetchone()[0]; oid=c.execute('INSERT INTO orders(product_id,quantity,total_cents) VALUES(%s,%s,%s) RETURNING id',(o.product_id,o.quantity,total)).fetchone()[0]
 return {'id':oid,'product_id':o.product_id,'quantity':o.quantity,'total_cents':total,'remaining_stock':left}
@app.get('/api/orders')
def orders():
 with db() as c: rows=c.execute('SELECT id,product_id,quantity,total_cents,created_at FROM orders ORDER BY id DESC').fetchall()
 return [dict(zip(['id','product_id','quantity','total_cents','created_at'],r)) for r in rows]
@app.get('/metrics')
def metrics(): return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)
