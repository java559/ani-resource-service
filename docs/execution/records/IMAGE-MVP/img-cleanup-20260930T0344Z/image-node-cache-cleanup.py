import json,subprocess,socket,time
digest='sha256:66a6306db78bf2dbf3487f293aa8d6990d8e506fdffab9cc43fe422becf886e4'
image_id='sha256:30ecbe1509090b38ef3260f3609d64d8261230464eaa4d235901db496e9647fb'
prefix='172.16.101.10:30003/'
owned={prefix+p+'/fixture@'+digest for p in ['t-imgp-0930-0200-a','t-imgp-0930-0200-b','imgp-0930-0200-platform']}
def run(args):return subprocess.check_output(['sudo','-n']+args,text=True)
rows=run(['ctr','-n','k8s.io','images','ls']).splitlines()[1:]
matching=[line.split()[0] for line in rows if digest in line.split()]
assert matching and set(matching)<=owned|{image_id},matching
containers=json.loads(run(['crictl','ps','-a','-o','json']))['containers']
assert not any(c.get('imageRef') in [image_id,digest] or c.get('image',{}).get('image') in owned|{image_id,digest} for c in containers),'image in use'
sandboxes=json.loads(run(['crictl','pods','-o','json']))['items']
assert not any(p.get('metadata',{}).get('namespace','').startswith('imgp-0930-0200') for p in sandboxes),'test sandbox remains'
info=json.loads(run(['crictl','inspecti',image_id]))['status']
assert info['id']==image_id and not info.get('pinned',False)
assert not info.get('repoTags') and set(info.get('repoDigests',[]))<=owned,info.get('repoDigests')
removed=run(['crictl','rmi',image_id]).strip()
after=run(['ctr','-n','k8s.io','images','ls']).splitlines()[1:]
assert not any(digest in row or 'imgp-0930-0200' in row or image_id in row for row in after)
cri=json.loads(run(['crictl','images','-o','json']))['images']
assert all(i['id']!=image_id for i in cri)
print(json.dumps({'host':socket.gethostname(),'removed_refs':matching,'image_id':image_id,'manifest_digest':digest,'in_use':False,'cri_remove_result':removed,'remaining_matching_images':0,'test_sandboxes':0,'finished_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())},indent=2))
