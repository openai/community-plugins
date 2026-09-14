// SPDX-License-Identifier: Apache-2.0
// The verified runtime supplies the JVM arguments and classpath. Inherited loader
// overrides must not add code or change those arguments before native admission.
export function javaEnvironment(source = process.env) {
  const environment = { ...source }, ignored = [];
  for (const key of Object.keys(environment)) {
    if (/^(?:JAVA_TOOL_OPTIONS|JDK_JAVA_OPTIONS|_JAVA_OPTIONS|CLASSPATH|LD_.*|DYLD_.*)$/i.test(key)) {
      ignored.push(key);
      delete environment[key];
    }
  }
  return { environment, ignored: ignored.sort() };
}
