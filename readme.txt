Build and push
--------------
sudo docker build -t shashikanthvh/api-adapter ./api-service/ ; sudo docker push shashikanthvh/api-adapter

Existing API retained
---------------------
GET/POST /adapter/lccnsub
- Existing Devtron forwarding behavior is retained.
- Uses DEVTRON_URL, REST_REQ_BODY, DEVTRON_TOKEN and DEVTRON_VERIFY_SSL.

Example:
curl --location 'http://128.224.48.206:30085/adapter/lccnsub' \
--header 'Content-Type: application/json' \
--data ''

New PM flow
-----------
1. On adapter startup, the service creates a PM job in Tacker when:
   PM_JOB_CREATE_ON_STARTUP=true

2. PM job creation uses config only:
   TACKER_PM_JOB_URL
   TACKER_PM_JOB_BODY
   TACKER_VERIFY_SSL
   TACKER_USERNAME / TACKER_PASSWORD or TACKER_TOKEN, if required

3. Tacker validates callback URL:
   GET /api/pm/callback
   Response: 204 No Content

4. Tacker sends PM notification:
   POST /api/pm/callback

5. Adapter extracts:
   _links.performanceReport.href

6. Adapter calls Tacker report URL, for example:
   GET /vnfpm/v2/pm_jobs/{pmJobId}/reports/{reportId}

7. Adapter converts the PM report into Prometheus Pushgateway format and posts to:
   {PUSHGATEWAY_BASE_URL}/metrics/job/{job_name}

Metric routing
--------------
performanceMetric contains VCpu/cpu       -> job=cpu_metrics, metric=cpu_utilisation
performanceMetric contains VDisk/disk     -> job=disk_metrics, metric=disk_utilisation
performanceMetric contains VMemory/memory -> job=memory_metrics, metric=memory_utilisation
performanceMetric contains network/rx/tx  -> job=network_metrics, metric=network_utilisation
unknown metric                            -> job=generic_performance_metrics, metric=sanitized performanceMetric

Example generated Pushgateway payload
-------------------------------------
cpu_utilisation{object_type="Vnf",instance_id="a0205e7c-fdeb-4f6c-b266-962246e32626",performance_metric="VCpuUsageMeanVnf.a0205e7c-fdeb-4f6c-b266-962246e32626"} 1.0002889206831795

Manual PM job creation trigger
------------------------------
curl -X POST http://localhost:6000/adapter/pm/jobs

Direct performance metric test API
----------------------------------
curl -X POST http://localhost:6000/adapter/performance-metrics \
  -H "Content-Type: application/json" \
  -d '{
    "entries": [{
      "objectType": "Vnf",
      "objectInstanceId": "a0205e7c-fdeb-4f6c-b266-962246e32626",
      "performanceMetric": "VCpuUsageMeanVnf.a0205e7c-fdeb-4f6c-b266-962246e32626",
      "performanceValues": [{
        "timeStamp": "2024-09-24T14:22:27Z",
        "value": "1.0002889206831795"
      }]
    }]
  }'

Tacker callback test
--------------------
curl -X POST http://localhost:6000/api/pm/callback \
  -u ubuntu:ubuntu \
  -H "Content-Type: application/json" \
  -d '{
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
  }'

Prometheus/Alertmanager to Devtron action trigger
-------------------------------------------------
Old heal scenario and new migrate scenario both use the same generic code path.
There is no duplicate heal/migrate handler logic.

Existing heal endpoints continue to work and default to action=heal:
POST /adapter/prometheus/alerts
POST /api/prometheus/alerts

New explicit action endpoints:
POST /adapter/prometheus/alerts/heal
POST /adapter/prometheus/alerts/migrate
POST /api/prometheus/alerts/heal
POST /api/prometheus/alerts/migrate

You can also trigger an action using either:
- query parameter: /adapter/prometheus/alerts?action=migrate
- alert label/annotation: action=migrate

The adapter resolves the action and calls DEVTRON_ACTIONS[action].url with DEVTRON_ACTIONS[action].body.
Configure heal, migrate, or future actions in ConfigMap under DEVTRON_ACTIONS without changing code.

Heal example using existing endpoint:
curl -X POST http://localhost:6000/adapter/prometheus/alerts \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"labels":{"alertname":"HighCpuUsage","instance_id":"a0205e7c-fdeb-4f6c-b266-962246e32626"}}]}'

Migrate example using explicit endpoint:
curl -X POST http://localhost:6000/adapter/prometheus/alerts/migrate \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"labels":{"alertname":"HighCpuUsage","instance_id":"a0205e7c-fdeb-4f6c-b266-962246e32626"}}]}'

Migrate example using alert label:
curl -X POST http://localhost:6000/adapter/prometheus/alerts \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"labels":{"alertname":"HighCpuUsage","action":"migrate","instance_id":"a0205e7c-fdeb-4f6c-b266-962246e32626"}}]}'

Mock Pushgateway for temporary local testing
-------------------------------------------
If real Pushgateway is not running, set:
PUSHGATEWAY_BASE_URL=http://localhost:6000

Then the adapter will post to its built-in mock endpoint:
POST /metrics/job/<job_name>

Inspect mock Pushgateway records:
curl http://localhost:6000/mock-pushgateway/store

Clear mock Pushgateway records:
curl -X DELETE http://localhost:6000/mock-pushgateway/store

Inspect PM callback processing history:
curl http://localhost:6000/adapter/pm/history
