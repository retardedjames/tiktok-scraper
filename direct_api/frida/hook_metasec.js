/*
 * hook_metasec.js — discover + trace TikTok Lite's signing JNI methods.
 *
 * Targets libmetasec_ov.so's RegisterNatives call inside JNI_OnLoad.
 *
 * Algorithm:
 *   1. Install hooks as early as possible: dlopen (catches when libmetasec_ov loads),
 *      and libart's RegisterNatives (catches ALL native method registrations).
 *   2. When libmetasec_ov triggers RegisterNatives, dump every {name, sig, fnPtr}
 *      registered under the metasec class.
 *   3. The method named "leviTransform" / "sign" / "metaSec*" is our target.
 *   4. After discovery, user can run rpc.exports.sign(query, ...) from Python.
 *
 * Usage:
 *   frida -U -f com.tiktok.lite.go -l hook_metasec.js --runtime=v8
 *   (spawn-attach so we catch JNI_OnLoad at app init)
 */

const TARGET_SO = "libmetasec_ov.so";
let metasecMod = null;
let signingFns = {};  // name -> NativePointer

function log(msg) { console.log("[mx] " + msg); }

// ----------------------------------------------------------------
// dlopen tracking — know when libmetasec_ov loads
// ----------------------------------------------------------------
for (const fname of ["dlopen", "__dl_dlopen", "android_dlopen_ext", "__loader_dlopen"]) {
    const addr = Module.findGlobalExportByName(fname);
    if (!addr) continue;
    Interceptor.attach(addr, {
        onEnter(args) { this.path = args[0] ? args[0].readCString() : null; },
        onLeave() {
            if (!this.path) return;
            if (this.path.includes(TARGET_SO)) {
                log("DLOPEN " + this.path);
                setImmediate(() => bindMetasecModule());
            }
        }
    });
}

function bindMetasecModule() {
    if (metasecMod) return;
    const m = Process.findModuleByName(TARGET_SO);
    if (!m) { log("post-dlopen: module still not visible, will retry"); return; }
    metasecMod = m;
    log("bound " + TARGET_SO + " @ " + m.base + " size=0x" + m.size.toString(16));
    log("exports:");
    for (const e of m.enumerateExports()) log("  " + e.type + " " + e.name + " @ " + e.address);
    log("symbols (top 30):");
    for (const s of m.enumerateSymbols().slice(0, 30)) log("  " + s.type + " " + s.name + " @ " + s.address);
    // Don't try to hook JNI_OnLoad directly — too late (it's already running).
    // Instead, we rely on RegisterNatives hook for the method table dump.
}

// ----------------------------------------------------------------
// RegisterNatives hook — discovers JNI method registrations
// ----------------------------------------------------------------
function installRegisterNativesHook() {
    // libart exports RegisterNatives under a mangled name. Find it via ApiResolver.
    const resolver = new ApiResolver("module");
    const matches = resolver.enumerateMatches("exports:libart.so!*RegisterNatives*");
    log("libart RegisterNatives candidates: " + matches.length);
    for (const m of matches) log("  " + m.name + " @ " + m.address);

    // Standard ART signature: art::JNI::RegisterNatives(_JNIEnv*, _jclass*, JNINativeMethod const*, int)
    let target = matches.find(m => m.name.indexOf("RegisterNatives") !== -1);
    if (!target && matches.length) target = matches[0];

    if (!target) {
        log("no RegisterNatives symbol — falling back to scan");
        return;
    }

    log("installing RegisterNatives hook @ " + target.address);
    Interceptor.attach(target.address, {
        onEnter(args) {
            const env = args[0];
            const clazz = args[1];
            const methods = args[2];
            const count = args[3].toInt32();

            // Get class name via Java.vm if available
            let clsName = "?";
            try {
                const env2 = Java.vm.tryGetEnv();
                if (env2) {
                    const getObjCls = env2.findClass("java/lang/Object").getClass();
                    // Simpler: use the wrapper
                    const Class = Java.use("java.lang.Class");
                    const clsObj = Java.cast(clazz, Class);
                    clsName = clsObj.getName();
                }
            } catch (e) { clsName = "<err:" + e.message + ">"; }

            // Filter: only print when registration involves metasec code
            const pages = [];
            for (let i = 0; i < count; i++) {
                const m = methods.add(i * Process.pointerSize * 3);
                const fn = m.add(Process.pointerSize * 2).readPointer();
                const mod = Process.findModuleByAddress(fn);
                if (mod && mod.name.includes("metasec")) pages.push(true);
            }
            const isInteresting = pages.length > 0 || /metasec|MSSdk|mssdk|MetaSec/i.test(clsName);
            if (!isInteresting) return;

            log("=== RegisterNatives class=" + clsName + " count=" + count + " ===");
            for (let i = 0; i < count; i++) {
                const m = methods.add(i * Process.pointerSize * 3);
                const name = m.readPointer().readCString();
                const sig  = m.add(Process.pointerSize).readPointer().readCString();
                const fn   = m.add(Process.pointerSize * 2).readPointer();
                const mod  = Process.findModuleByAddress(fn);
                const where = mod ? (mod.name + "+0x" + fn.sub(mod.base).toString(16)) : ("?@" + fn);
                log("  [" + i + "] " + name + sig + " -> " + where);
                signingFns[clsName + "." + name] = fn;
            }
        }
    });
}

// ----------------------------------------------------------------
// Generic signing-call tracer — once RegisterNatives hook has
// populated signingFns, we can trace specific invocations.
// ----------------------------------------------------------------
function traceSigningCalls() {
    for (const [key, fn] of Object.entries(signingFns)) {
        if (!/sign|levi|transform|Metasec/i.test(key)) continue;
        log("tracing " + key + " @ " + fn);
        Interceptor.attach(fn, {
            onEnter(args) {
                this.key = key;
                this.args = [args[0], args[1], args[2], args[3], args[4], args[5]];
                log("CALL " + key);
                for (let i = 0; i < 6; i++) {
                    try {
                        const a = args[i];
                        log("  arg[" + i + "] = " + a);
                    } catch (_) {}
                }
            },
            onLeave(rv) {
                log("RETURN " + this.key + " = " + rv);
            }
        });
    }
}

// ----------------------------------------------------------------
// Bootstrap
// ----------------------------------------------------------------
setImmediate(() => {
    log("bootstrap");
    Java.perform(() => {
        log("Java.perform ready");
        installRegisterNativesHook();
        bindMetasecModule(); // in case it's already loaded

        // Expose an RPC so we can trigger tracing on demand later
        rpc.exports = {
            list: () => Object.keys(signingFns),
            trace: () => { traceSigningCalls(); return "tracing enabled"; },
            dumpMetasec: () => {
                if (!metasecMod) return { error: "libmetasec_ov not loaded" };
                return {
                    base: metasecMod.base.toString(),
                    size: metasecMod.size,
                    path: metasecMod.path,
                };
            }
        };
    });
});
