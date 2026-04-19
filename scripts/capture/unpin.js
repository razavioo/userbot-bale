// Nasim-Bale TLS unpinning hook.
//
// Targets (ordered by likelihood):
//  1. OkHttp CertificatePinner.check → no-op
//  2. Nasim's own ir.nasim.core.network.sslpinning.* (pattern match)
//  3. X509TrustManagerExtensions.checkServerTrusted → bypass
//  4. javax.net.ssl.TrustManagerFactory / default X509 TM
//
// Goal: pass-through so the Android platform TrustManager (which trusts
// our user-installed mitmproxy CA, thanks to the network_security_config
// injected by objection patchapk) is what decides.

Java.perform(function () {
    try {
        var CertificatePinner = Java.use('okhttp3.CertificatePinner');
        CertificatePinner.check.overload('java.lang.String', 'java.util.List').implementation = function (a, b) {
            console.log('[unpin] OkHttp CertificatePinner.check(' + a + ') -> no-op');
        };
    } catch (e) { console.log('[unpin] OkHttp not found (ok)'); }

    // Nasim TLS hash check (from decompile)
    ['ir.nasim.core.network.util.TlsHashItem',
     'ir.nasim.core.network.util.TlsHash'].forEach(function (cn) {
        try {
            var C = Java.use(cn);
            Object.getOwnPropertyNames(C.__proto__).forEach(function (m) {
                if (/^(check|matches|verify|valid)/i.test(m)) {
                    try {
                        C[m].overloads.forEach(function (ov) {
                            ov.implementation = function () {
                                console.log('[unpin] ' + cn + '.' + m + ' -> true');
                                return true;
                            };
                        });
                    } catch (e2) {}
                }
            });
            console.log('[unpin] hooked ' + cn);
        } catch (e) {}
    });

    // Default X509TrustManager path — make it trust everything so the
    // platform's user-CA-augmented store decides. Necessary when the
    // network_security_config didn't make it into every code path.
    try {
        var TM = Java.use('javax.net.ssl.X509TrustManager');
        var SSLContext = Java.use('javax.net.ssl.SSLContext');
        var TrustAll = Java.registerClass({
            name: 'com.baleobala.TrustAllTM',
            implements: [TM],
            methods: {
                checkClientTrusted: function () {},
                checkServerTrusted: function () {},
                getAcceptedIssuers: function () { return []; },
            },
        });
        var tmArray = [TrustAll.$new()];
        SSLContext.init.overload('[Ljavax.net.ssl.KeyManager;', '[Ljavax.net.ssl.TrustManager;', 'java.security.SecureRandom').implementation = function (km, _tm, sr) {
            console.log('[unpin] SSLContext.init -> trust-all TM');
            this.init(km, tmArray, sr);
        };
    } catch (e) { console.log('[unpin] SSLContext hook failed: ' + e); }

    // Conscrypt platform pinner (newer OkHttp)
    try {
        var Platform = Java.use('okhttp3.internal.platform.Platform');
        if (Platform && Platform.trustManager) {
            console.log('[unpin] okhttp Platform present (not hooked; relying on CertificatePinner)');
        }
    } catch (e) {}

    console.log('[unpin] hooks installed');
});
