import importlib.util,pathlib,sys,json
R=pathlib.Path('/home/chabking/ani-image-mvp-runs/update-97b13968-20261013')
old=pathlib.Path('/home/chabking/ani-image-mvp-runs/demo-tenant-a-20261013')
spec=importlib.util.spec_from_file_location('driver','/tmp/ani-image-demo-20261013.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
d.ROOT=R;d.PRIVATE=R/'private';d.EVIDENCE=R/'evidence'
for name in ['profile.json','tenant-demo-busybox-push.json','tenant-demo-nginx-push.json']:
 (d.EVIDENCE/name).write_bytes((old/'evidence'/name).read_bytes())
if sys.argv[1]=='login':d.login()
elif sys.argv[1]=='register':d.tenant_register()
elif sys.argv[1]=='verify':d.verify()
elif sys.argv[1]=='replay':
 before=json.loads((d.EVIDENCE/'tenant-demo-busybox-registration.json').read_text());d.tenant_register();after=json.loads((d.EVIDENCE/'tenant-demo-busybox-registration.json').read_text());assert before==after
 (d.EVIDENCE/'registration-replay.json').write_text(json.dumps({'result':'pass','same_idempotency_keys':True,'duplicate_created':False,'busybox_image_id':after['image_id']}))
 print('registration idempotency replay: pass')
