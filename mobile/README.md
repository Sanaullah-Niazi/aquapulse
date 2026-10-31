# mobile/

The AquaPulse Android app (FR-41), built with Flutter. One app for residents and building owners: after login, the server role decides which screens appear.

Guide with every step for your Mac: [docs/milestones/M9-android-app.md](../docs/milestones/M9-android-app.md).

```text
lib/
  main.dart            starts the app: saved login, or the login screen
  app_state.dart       who is logged in (login, 2FA code, logout)
  live.dart            the live WebSocket (readings, valve answers, alerts, bills)
  format.dart          numbers, dates, the Safe / Check / Unsafe pH label
  api/
    client.dart        HTTP calls, token refresh, error messages
    token_store.dart   refresh token + server address in secure storage (Keystore)
    server.dart        server address check, trusted certificates
    models.dart        the shapes of the API's answers
  screens/
    shell.dart         role-based navigation
    shared/            login, server, bills, alerts, profile and notifications
    resident/          my home, my usage, water quality
    owner/             owner screens
  widgets/             valve control, charts, alert list, small building blocks
test/                  widget tests against a fake API (no phone, no server)
```

Commands (from the repository root):

```bash
make app-test      # flutter analyze + widget tests
make apk           # release APK that also trusts your Mac's AquaPulse CA
make apk-install   # install on the running emulator / USB phone, connect it to your Mac
```
