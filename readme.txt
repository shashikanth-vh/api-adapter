Build and push
--------------
sudo docker build -t shashikanthvh/api-adapter ./api-service/ ; sudo docker push shashikanthvh/api-adapter

Purpose
-------
This adapter retains the existing /adapter/lccnsub forwarding functionality and adds the actual PM adapter flow:

1. Create PM job in Tacker.
2. Receive PM callback notification from Tacker.
3. Fetch PM report from Tacker.
4. Convert PM report entries to Prometheus Pushgateway format.
5. Receive Prometheus/Alertmanager alerts.
6. Invoke the configured Devtron job for heal or migrate.

Important responsibility boundary
---------------------------------
The adapter does not perform Tacker heal or migrate directly.

For remediation actions, the adapter only invokes Devtron. The Devtron job/pipeline then performs the actual heal or migrate operation and may internally call Tacker.

The adapter still calls Tacker for PM-related APIs only:

- POST /vnfpm/v2/pm_jobs
- GET /vnfpm/v2/pm_jobs/{pmJobId}/reports/{reportId}

Tacker authentication mechanism
-------------------------------
For PM job creation and PM report fetch, the adapter follows the same mechanism as the provided PM scripts:

1. Obtain Keystone token from KEYSTONE_URL.
2. Send Tacker PM APIs with headers:
   Accept: application/json
   Content-Type: application/json
   User-Agent: python-tackerclient
   Version: 2.0.0
   X-Auth-Token: <Keystone token>

Existing API retained
---------------------
GET/POST /adapter/lccnsub

- Existing Devtron forwarding behavior is retained.
- Uses DEVTRON_URL, REST_REQ_BODY, DEVTRON_TOKEN and DEVTRON_VERIFY_SSL.

PM Job creation flow
--------------------
The adapter can create a Tacker PM job automatically on startup when:

PM_JOB_CREATE_ON_STARTUP=true

It can also be triggered manually:

POST /adapter/pm/jobs

The adapter reads these from ConfigMap:

TACKER_PM_JOB_URL
TACKER_PM_JOB_BODY

It then performs:

POST http://<TACKER_HOST>:9890/vnfpm/v2/pm_jobs

The PM job body is fully configurable in TACKER_PM_JOB_BODY, including:

- objectType
- objectInstanceIds
- performanceMetric
- performanceMetricGroup
- collectionPeriod
- reportingPeriod
- reportingBoundary
- callbackUri
- callback Basic Auth credentials
- Prometheus host
- Alertmanager host
- alertRuleConfigPath
- prometheusReloadApiEndpoint

Tacker callback validation
--------------------------
Tacker validates the callback endpoint using:

GET /api/pm/callback

The adapter returns:

204 No Content

If PM_CALLBACK_USERNAME/PM_CALLBACK_PASSWORD are configured, Basic Auth is validated.

Tacker PM notification handling
-------------------------------
Tacker sends the PM notification to:

POST /api/pm/callback

Expected body:

{
  "id": "6d0c5e29-093d-4945-84df-768c062972b1",
  "notificationType": "PerformanceInformationAvailableNotification",
  "timeStamp": "2026-05-11T13:23:22Z",
  "pmJobId": "4a876fea-b0c2-4471-899c-2ab02f2cce62",
  "objectType": "Vnf",
  "objectInstanceId": "7beb68bb-6b40-47e2-9e41-6312689407c6",
  "_links": {
    "performanceReport": {
      "href": "http://127.0.0.1:9890/vnfpm/v2/pm_jobs/4a876fea-b0c2-4471-899c-2ab02f2cce62/reports/fae1a25e-2761-4140-8f19-c095c83ee7d7"
    }
  }
}

The adapter extracts:

_links.performanceReport.href

Then it fetches the PM report from Tacker using:

GET /vnfpm/v2/pm_jobs/{pmJobId}/reports/{reportId}

PM report to Pushgateway
------------------------
The adapter expects the Tacker report to contain entries like:

{
  "entries": [
    {
      "objectType": "Vnf",
      "objectInstanceId": "a0205e7c-fdeb-4f6c-b266-962246e32626",
      "performanceMetric": "VCpuUsageMeanVnf.a0205e7c-fdeb-4f6c-b266-962246e32626",
      "performanceValues": [
        {
          "timeStamp": "2024-09-24T14:22:27Z",
          "value": "1.0002889206831795"
        }
      ]
    }
  ]
}

The adapter converts it to Prometheus Pushgateway text format and posts to:

POST ${PUSHGATEWAY_BASE_URL}/metrics/job/{job_name}

For CPU:

POST http://localhost:9091/metrics/job/cpu_metrics

Body:

cpu_utilisation{object_type="Vnf",instance_id="a0205e7c-fdeb-4f6c-b266-962246e32626",performance_metric="VCpuUsageMeanVnf.a0205e7c-fdeb-4f6c-b266-962246e32626"} 1.0002889206831795

Metric routing
--------------
performanceMetric contains VCpu/cpu       -> job=cpu_metrics, metric=cpu_utilisation
performanceMetric contains VDisk/disk     -> job=disk_metrics, metric=disk_utilisation
performanceMetric contains VMemory/memory -> job=memory_metrics, metric=memory_utilisation
performanceMetric contains network/rx/tx  -> job=network_metrics, metric=network_utilisation
unknown metric                            -> job=generic_performance_metrics, metric=sanitized performanceMetric

Direct performance metric API
-----------------------------
This API can be used when the caller already has the PM report payload:

POST /adapter/performance-metrics

Alert action flow: heal and migrate
-----------------------------------
Prometheus/Alertmanager can call:

POST /adapter/prometheus/alerts
POST /adapter/prometheus/alerts/heal
POST /adapter/prometheus/alerts/migrate
POST /api/prometheus/alerts
POST /api/prometheus/alerts/heal
POST /api/prometheus/alerts/migrate

The default action is heal.

Action can also be selected using:

- query parameter: /adapter/prometheus/alerts?action=migrate
- alert label: "action": "migrate"

There is one common action dispatcher. Heal and migrate do not have duplicate handler code.

Devtron action invocation
-------------------------
For both heal and migrate, the adapter calls the configured Devtron job API from DEVTRON_ACTIONS.

Example DEVTRON_ACTIONS:

{
  "heal": {
    "type": "devtron",
    "url": "https://preview.devtron.ai/orchestrator/app/ci-pipeline/trigger",
    "verify_ssl": false,
    "body": {
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
        "runtimePluginVariables": [
          {
            "name": "ACTION",
            "value": "heal"
          }
        ]
      }
    }
  },
  "migrate": {
    "type": "devtron",
    "url": "https://preview.devtron.ai/orchestrator/app/ci-pipeline/trigger",
    "verify_ssl": false,
    "body": {
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
        "runtimePluginVariables": [
          {
            "name": "ACTION",
            "value": "migrate"
          }
        ]
      }
    }
  }
}

PM history
----------
Inspect PM callback processing history:

GET /adapter/pm/history

Clear PM history:

DELETE /adapter/pm/history

Cross-check with Performance Management Flow document
-----------------------------------------------------
The latest implementation has been cross-checked against the supplied PM design document:

- PM job is created using POST /vnfpm/v2/pm_jobs.
- Callback validation endpoint GET /api/pm/callback returns 204 No Content.
- PM notification endpoint POST /api/pm/callback extracts _links.performanceReport.href.
- PM report is fetched using GET /vnfpm/v2/pm_jobs/{pmJobId}/reports/{reportId}.
- Report entries are converted to Prometheus Pushgateway text format.
- Heal and migrate are not executed directly by the adapter. The adapter only invokes the configured Devtron job API. Devtron job logic is responsible for invoking Tacker heal or migrate internally.

Devtron action body handling
----------------------------
The adapter sends DEVTRON_ACTIONS.<action>.body exactly as configured. It does not inject extra top-level fields into the Devtron request body by default, because Devtron trigger APIs may reject unexpected fields.

If an action later needs alert context in the request body, enable it explicitly per action:

"include_alert_payload": true

