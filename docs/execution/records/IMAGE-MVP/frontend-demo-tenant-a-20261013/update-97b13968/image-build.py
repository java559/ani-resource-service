import hashlib,json,pathlib,shutil,subprocess
r=pathlib.Path('/home/chabking/ani-image-mvp-runs/update-97b13968-20261013');c=r/'images/context';c.mkdir()
shutil.copy2(r/'bin/resource',c/'server');shutil.copy2('/etc/ssl/certs/ca-certificates.crt',c/'ca-certificates.crt')
notice=''
for name in ['LICENSE','LICENSE.md','THIRD_PARTY_NOTICES.md']:
 if (r/'source'/name).is_file():shutil.copy2(r/'source'/name,c/name);notice+='COPY '+name+' /licenses/'+name+'\n'
sha='97b13968d156d65fe492cb583ec9920d21d0e57a'
(c/'Dockerfile').write_text('FROM scratch\nUSER 65532:65532\nCOPY server /app/resource-service\nCOPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt\n'+notice+'ENTRYPOINT ["/app/resource-service"]\nLABEL org.opencontainers.image.source="https://github.com/zhangzhe-ctrl/ani-resource-service" org.opencontainers.image.revision="'+sha+'"\n')
tag='localhost/ani-resource:'+sha[:12]+'-20261013';archive=r/'images/resource.oci.tar'
subprocess.run(['podman','build','--network=none','-t',tag,str(c)],check=True)
subprocess.run(['podman','save','--format','oci-archive','-o',str(archive),tag],check=True)
info=json.loads(subprocess.check_output(['skopeo','inspect','oci-archive:'+str(archive)]))
record={'source_sha':sha,'tag':tag,'digest':info['Digest'],'reference':tag+'@'+info['Digest'],'archive':str(archive),'archive_sha256':hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),'binary_sha256':hashlib.sha256((r/'bin/resource').read_bytes()).hexdigest(),'architecture':info['Architecture'],'os':info['Os'],'labels':info['Labels']}
(r/'evidence/image.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2))
