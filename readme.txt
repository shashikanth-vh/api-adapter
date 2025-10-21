sudo docker build -t shashikanthvh/api-adapter ./api-service/ ; sudo docker push shashikanthvh/api-adapter





curl --location 'http://128.224.48.206:30085/adapter/lccnsub' \
--header 'Content-Type: application/json' \
--data ''


curl --location 'http://128.224.48.206:30085/adapter/lccnsub' \
--header 'Content-Type: application/json' \
--data '{
    "pipelineId": 337,
    "ciPipelineMaterials": [
        {
            "Id": 344,
            "GitCommit": {
                "Commit": "1df32f437520f3d5f7d379511bdcb76ebf851cfb"
            }
        }
    ],
    "invalidateCache": false,
    "pipelineType": "CI_BUILD",
    "runtimeParams": {
        "runtimePluginVariables": []
    }
}'


