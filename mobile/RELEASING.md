# Releasing the mobile app

Builds run on EAS (Expo Application Services). Two independent version numbers:

| Number | Where it lives | Who bumps it |
|---|---|---|
| **Version** (user-facing semver, `1.0.0`) | `app.json` → `expo.version`, mirrored in `package.json` | You, by hand, per release (`releaseConfig.test.ts` fails if they drift) |
| **Build number** (Android `versionCode`, iOS `buildNumber`) | EAS server (`appVersionSource: remote`) | EAS, automatically, on every `production` build (`autoIncrement`) |

Never hard-code `versionCode` / `buildNumber` in `app.json`; the remote counter is the single source of truth.

Semver: patch = fixes only, minor = new screens/features, major = breaking change to the API contract the app depends on.

## Profiles (`eas.json`)

| Profile | Output | Use |
|---|---|---|
| `preview` | Android `.apk`, internal distribution | Testers: install directly from the EAS link |
| `production` | Android `.aab`, build number auto-incremented | Play Store submission |

Both bake `EXPO_PUBLIC_API_URL=https://educore.makan.live/api/v1` into the bundle. A release build with no URL, a non-https URL or one not ending in `/api/v1` fails at launch (`resolveApiBase`, `src/services/apiConfig.ts`); only a development bundle falls back to a local server (`10.0.2.2:8000` on the Android emulator, `localhost:8000` on the iOS simulator).

## One-time setup

```bash
cd mobile
npm ci
npx eas-cli login          # or set EXPO_TOKEN in CI
npx eas-cli init           # links the project, writes extra.eas.projectId into app.json — commit it
```

Android signing: let EAS generate and hold the keystore on the first build (`eas credentials` to inspect). Losing it means you can never update the Play listing, so do not skip the backup prompt.

## Cut a release

```bash
cd mobile
npm test && npm run typecheck && npx expo-doctor
# 1. bump "version" in app.json AND package.json
# 2. build
npx eas-cli build --platform android --profile preview       # testers
npx eas-cli build --platform android --profile production    # store
# 3. tag the commit that produced the build
git tag mobile-v1.0.0 && git push origin mobile-v1.0.0
```

Inspect or correct the remote counter: `npx eas-cli build:version:get -p android --profile production` / `build:version:set`.

## Not yet wired

- **Android push (FCM):** add `google-services.json` and set `android.googleServicesFile` in `app.json` (see Expo docs, "FCM credentials"), otherwise token registration fails silently in release builds.
- **iOS:** needs an Apple Developer account; `bundleIdentifier` is already set. Add an `ios` block to the profiles when ready.
- **Icons:** `assets/` holds generated placeholders; swap in the brand icon (1024² opaque icon, 1024² transparent adaptive foreground with the glyph inside the centre 66%, 96² white-on-transparent notification icon).
