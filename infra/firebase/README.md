# infra/firebase/

Optional: push notifications to the Android app (M9, FR-60). Without the files below, push is simply off. Alerts still show in the app (live) and come by email.

Firebase Cloud Messaging is free (the "Spark" plan, no card needed).

Two files go in two places. Both are secret-ish and **git-ignored**:

| File | Where | Used by |
|---|---|---|
| `firebase-service-account.json` | this folder (`infra/firebase/`) | the server: the worker sends pushes, the API says push is available |
| `google-services.json` | `mobile/android/app/` | the Android app: `make apk` builds it in, so the app can get a push token |

How to get them: see "Part 3" in [docs/milestones/M9-android-app.md](../../docs/milestones/M9-android-app.md).

After adding or changing the server file: `docker compose up -d --force-recreate api worker`.
