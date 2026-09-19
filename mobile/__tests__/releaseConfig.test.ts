import assert from 'node:assert';
import { existsSync, readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { withGoogleServices } from '../app.config.ts';

const readJson = (rel: string) => JSON.parse(readFileSync(new URL(`../${rel}`, import.meta.url), 'utf8'));

const app = readJson('app.json').expo;
const pkg = readJson('package.json');
const eas = readJson('eas.json');

describe('release config', () => {
  it('keeps app.json version as strict semver, mirrored in package.json', () => {
    assert.match(app.version, /^\d+\.\d+\.\d+$/);
    assert.strictEqual(pkg.version, app.version);
  });

  it('leaves build numbers to EAS remote versioning', () => {
    assert.strictEqual(eas.cli.appVersionSource, 'remote');
    assert.strictEqual(eas.build.production.autoIncrement, true);
    assert.strictEqual(app.android.versionCode, undefined);
    assert.strictEqual(app.ios.buildNumber, undefined);
  });

  it('points every build profile at a non-loopback https API', () => {
    for (const name of ['preview', 'production']) {
      assert.match(eas.build[name].env.EXPO_PUBLIC_API_URL, /^https:\/\/(?!localhost|10\.0\.2\.2)/, name);
    }
  });

  it('offers an iOS Simulator build that needs no Apple account', () => {
    assert.strictEqual(eas.build['preview-simulator'].extends, 'preview');
    assert.strictEqual(eas.build['preview-simulator'].ios.simulator, true);
  });

  it('ships every asset app.json references, and the entry module', () => {
    const paths = [
      app.icon,
      app.android.adaptiveIcon.foregroundImage,
      app.plugins.find((p: unknown) => Array.isArray(p) && p[0] === 'expo-notifications')[1].icon,
      pkg.main,
    ];
    for (const rel of paths) {
      assert.ok(existsSync(new URL(`../${rel}`, import.meta.url)), rel);
    }
  });

  it('vendors the SQLite source expo-sqlite would download at build time', () => {
    // sqlite.org is intermittently unreachable from EAS workers; the post-install hook seeds Gradle's download cache.
    const gradle = readFileSync(new URL('../node_modules/expo-sqlite/android/build.gradle', import.meta.url), 'utf8');
    const version = /def SQLITE_VERSION = '(\d+)'/.exec(gradle)?.[1];
    assert.ok(version, 'SQLITE_VERSION not found in expo-sqlite build.gradle');
    assert.ok(existsSync(new URL(`../vendor/sqlite/sqlite-amalgamation-${version}.zip`, import.meta.url)), version);
    assert.match(pkg.scripts['eas-build-post-install'], /vendor\/sqlite\/\*\.zip/);
  });

  it('asks for Face ID with an id-ID reason (iOS rejects the prompt without one)', () => {
    const plugin = app.plugins.find((p: unknown) => Array.isArray(p) && p[0] === 'expo-local-authentication');
    assert.ok(plugin?.[1].faceIDPermission);
  });
});

describe('android FCM credentials', () => {
  const base = { android: { package: 'id.sch.educore.guru' } };

  it('injects the EAS file variable as googleServicesFile, keeping the rest of android', () => {
    const out = withGoogleServices(base, { GOOGLE_SERVICES_JSON: '/eas/google-services.json' });
    assert.deepStrictEqual(out.android, { package: 'id.sch.educore.guru', googleServicesFile: '/eas/google-services.json' });
    assert.strictEqual((base.android as Record<string, unknown>).googleServicesFile, undefined);
  });

  it('refuses a production Android build that has no google-services.json', () => {
    assert.throws(
      () => withGoogleServices(base, { EAS_BUILD_PROFILE: 'production', EAS_BUILD_PLATFORM: 'android' }),
      /GOOGLE_SERVICES_JSON/,
    );
  });

  it('lets local, preview and iOS builds proceed without it', () => {
    assert.strictEqual(withGoogleServices(base, {}), base);
    assert.strictEqual(withGoogleServices(base, { EAS_BUILD_PROFILE: 'preview', EAS_BUILD_PLATFORM: 'android' }), base);
    assert.strictEqual(withGoogleServices(base, { EAS_BUILD_PROFILE: 'production', EAS_BUILD_PLATFORM: 'ios' }), base);
  });
});
