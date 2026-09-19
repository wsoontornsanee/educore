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
| `preview-simulator` | iOS Simulator `.app` (extends `preview`) | Run the iOS build without an Apple Developer account |
| `production` | Android `.aab` / iOS `.ipa`, build number auto-incremented | Play Store / TestFlight submission |

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

## Android push (FCM)

`app.config.ts` layers `android.googleServicesFile` over `app.json` from the `GOOGLE_SERVICES_JSON` EAS file variable, so the Firebase file never enters git. A `production` Android build **fails at config time** without it; `preview` and local builds proceed (push just won't register).

One-time, owner-side:
1. Firebase console: create a project, add an Android app with package `id.sch.educore.guru`, download `google-services.json`.
2. `npx eas-cli env:create --name GOOGLE_SERVICES_JSON --type file --value ./google-services.json --visibility sensitive --environment production --environment preview`
3. Firebase → Project settings → Service accounts → generate a private key, then `npx eas-cli credentials` → Android → Google Service Account → FCM V1 key. (Expo's push service needs this to relay to devices.)

## iOS

`bundleIdentifier` is set and `expo-local-authentication` declares its Face ID reason. Both profiles already build for iOS (`--platform ios`); nothing is iOS-specific except credentials.

- **No Apple account:** `npx eas-cli build --platform ios --profile preview-simulator`, then drag the `.app` into the Simulator.
- **Device / TestFlight (needs an Apple Developer Program account):** `eas device:create` (internal `preview` only), then `eas build --platform ios --profile production` and `eas submit --platform ios --profile production`; EAS creates the certificates and the APNs push key on the first run.
- Before the first TestFlight upload the owner should decide the export-compliance answer (`ios.infoPlist.ITSAppUsesNonExemptEncryption`); it is left unset so App Store Connect asks.

## Not yet wired

- **Icons:** `assets/` holds generated placeholders (a white "E" on brand red); swap in the brand icon (1024² opaque icon, 1024² transparent adaptive foreground with the glyph inside the centre 66%, 96² white-on-transparent notification icon).
- **CI:** no workflow builds on a `mobile-v*` tag yet (needs an `EXPO_TOKEN` secret).
