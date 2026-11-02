// SPDX-License-Identifier: GPL-3.0-or-later
// Run this verified local file through the native OpenPnP Scripts menu.
// The installer supplies explicit immutable bridge/runtime paths and SHA-256 values.
// There is one fixed Java entry point; no command, property or script executor is exposed by RPC.
var System = Java.type('java.lang.System');
var Paths = Java.type('java.nio.file.Paths');
var Files = Java.type('java.nio.file.Files');
var URLClassLoader = Java.type('java.net.URLClassLoader');
var MessageDigest = Java.type('java.security.MessageDigest');
var StringBuilder = Java.type('java.lang.StringBuilder');
if (!gui) throw new Error('Open the native simulator GUI before attaching Codex.');
var jarValue = System.getProperty('openpnp.codex.bridgeJar');
var expected = System.getProperty('openpnp.codex.bridgeSha256');
if (!jarValue || !Paths.get(jarValue).isAbsolute() || !expected || !/^[a-f0-9]{64}$/.test(String(expected)))
    throw new Error('Set the installed absolute bridge JAR path and its SHA-256.');
var jar = Paths.get(jarValue).toRealPath();
var digest = MessageDigest.getInstance('SHA-256').digest(Files.readAllBytes(jar));
var actual = new StringBuilder();
for (var i=0;i<digest.length;i++) { var octet=(digest[i] & 255).toString(16); actual.append(octet.length===1?'0'+octet:octet); }
if (String(actual.toString()) !== String(expected)) throw new Error('Installed bridge JAR digest mismatch.');
var parent = gui.getClass().getClassLoader();
try { parent.loadClass('org.openpnp.spi.base.ExternalExecutionControl'); }
catch (unsupported) { throw new Error('This GUI needs the explicit Codex ownership/action-observer native patch. Stock isolated simulator operation remains available.'); }
var loader = new URLClassLoader(Java.to([jar.toUri().toURL()],'java.net.URL[]'),parent);
var retained = false;
try {
    var entry = loader.loadClass('org.openpnp.codex.GuiBootstrap');
    retained = String(entry.getMethod('attach',URLClassLoader.class).invoke(null,loader)) === 'true';
} finally {
    if (!retained) loader.close();
}
