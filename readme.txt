smudigan@yow-smudigan-lx:~/shashi/opa$ kubectl create -f k8s/
deployment.apps/policy created
configmap/example-policy created
configmap/policy-config created
service/opa-service created
service/policy-api-service created

-----------------------------------------------------------------------------





gchawla@yow-gchawla-lx:~/shashi/opa/opa-1$ vi k8s/disk.rego

Insert policy:

gchawla@yow-gchawla-lx:~/shashi/opa/opa$ curl -X PUT --data-binary @k8s/disk.rego   192.168.49.2:30081/v1/policies/diskcheck
{}

send request to OPA to validate:
gchawla@yow-gchawla-lx:~/shashi/opa/opa-1$ curl -X POST 192.168.49.2:30081/v1/data/diskcheck/allow   -H "Content-Type: application/json"   -d '{"input": {"usage": 70}}'
{"result":false}

gchawla@yow-gchawla-lx:~/shashi/opa/opa-1$ curl -X POST 192.168.49.2:30081/v1/data/diskcheck/allow   -H "Content-Type: application/json"   -d '{"input": {"usage": 90}}'
{"result":true}
gchawla@yow-











gchawla@yow-gchawla-lx:~/shashi/opa/opa-2/k8s$  curl -X PUT  --data-binary @disk.rego  192.168.49.2:30081/v1/policies/disk
{}
gchawla@yow-gchawla-lx:~/shashi/opa/opa-2/k8s$  curl -X POST 192.168.49.2:30081/v1/data/disk/allow  -H "Content-Type: application/json"   -d '{"input": {"policy_name": "disk"}}'
{"result":false}
gchawla@yow-gchawla-lx:~/shashi/opa/opa-2/k8s$  curl -X POST 192.168.49.2:30081/v1/data/disk/allow  -H "Content-Type: application/json"   -d '{"input": {"policy_name": "disk","host_ip": "128.224.48.206"}}'
{"result":true}
gchawla@yow-gchawla-lx:~/shashi/opa/opa-2/k8s$  curl -X POST 192.168.49.2:30081/v1/data/disk/action_plan  -H "Content-Type: application/json"   -d '{"input": {"policy_name": "disk","host_ip": "128.224.48.206"}}'
{"result":{"check_after_each":{"body":{},"headers":{"Accept":"application/json","Authentication-Token":"{{authToken}}","Authorization":"Basic YWRtaW46V3JzMjAhN2N0bw==","Content-Type":"application/json","Tenant":"default_tenant"},"method":"POST","name":"Check Deployment Exists","url":"http://conductor-yow-triton-016.cto.wrs.com/api/v3.1/deployments/action_on_disk","wait_until_complete":true},"interval":5,"steps":[{"body":{"blueprint_id":"Disk_Action","inputs":{"file_extensions":"log,tmp","host_ip":"128.224.48.206","host_password":"wrcp_ssh_password","host_username":"sysadmin","move_to_path":"/opt/platform-backup/","option":"delete","target_path":"/scratch","timeout":1200}},"headers":{"Authentication-Token":"{{authToken}}","Authorization":"Basic YWRtaW46V3JzMjAhN2N0bw==","Content-Type":"application/json","Tenant":"default_tenant"},"method":"PUT","name":"Create Deployment","url":"http://conductor-yow-triton-016.cto.wrs.com/api/v3.1/deployments/action_on_disk"},{"body":{"deployment_id":"action_on_disk","workflow_id":"install"},"headers":{"Accept":"application/json","Authentication-Token":"{{authToken}}","Authorization":"Basic YWRtaW46V3JzMjAhN2N0bw==","Content-Type":"application/json","Tenant":"default_tenant"},"method":"POST","name":"Run Install Workflow","url":"http://conductor-yow-triton-016.cto.wrs.com/api/v3.1/executions"},{"body":{"deployment_id":"action_on_disk","workflow_id":"uninstall"},"headers":{"Accept":"application/json","Authentication-Token":"{{authToken}}","Authorization":"Basic YWRtaW46V3JzMjAhN2N0bw==","Content-Type":"application/json","Tenant":"default_tenant"},"method":"POST","name":"Run UnInstall Workflow","url":"http://conductor-yow-triton-016.cto.wrs.com/api/v3.1/executions"},{"body":{},"headers":{"Accept":"application/json","Authentication-Token":"{{authToken}}","Authorization":"Basic YWRtaW46V3JzMjAhN2N0bw==","Content-Type":"application/json","Tenant":"default_tenant"},"method":"DELETE","name":"Run UnInstall Workflow","url":"http://conductor-yow-triton-016.cto.wrs.com/api/v3.1/deployments/action_on_disk"}],"timeout":300}}
gchawla@yow-gchawla-lx:~/shashi/opa/opa-2/k8s$







docker build -t shashikanthvh/policy ./policy ; sudo docker push shashikanthvh/policy
kubectl delete -f k8s/
kubectl create -f k8s/

curl -X PUT  --data-binary @k8s/disk.rego  192.168.49.2:30081/v1/policies/disk

curl -X POST 192.168.49.2:30082/policy/disk  -H "Content-Type: application/json"   -d '{"input": {"policy_name": "disk","host_ip": "128.224.48.206"}}'
