/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;

/** Fresh child JVM per bounded completion case; no user installation or machine access. */
final class NativeCompletionCases {
 static void run(Class<?> suite,Path samples,String marker,String... cases)throws Exception{
  Path root=Files.createTempDirectory("openpnp-completion-cases-").toRealPath();
  JsonArray results=new JsonArray();int assertions=0;boolean passed=true;
  for(String name:cases){
   Path fixture=root.resolve(name);Files.createDirectory(fixture);Files.createDirectory(fixture.resolve("home"));Files.createDirectory(fixture.resolve("tmp"));Path log=root.resolve(name+".log");
   List<String> command=new ArrayList<>(Arrays.asList(Paths.get(System.getProperty("java.home"),"bin","java").toString(),
    "-Xmx512m","-XX:+ExitOnOutOfMemoryError","-Djava.awt.headless=true",
    "-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory",
    "-Duser.home="+fixture.resolve("home"),"-Djava.io.tmpdir="+fixture.resolve("tmp"),
    "--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED",
    "-cp",System.getProperty("java.class.path"),suite.getName(),name,fixture.toString(),samples.toRealPath().toString()));
   ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());
   builder.environment().keySet().removeIf(k->{String n=k.toUpperCase(Locale.ROOT);return n.startsWith("OPENPNP_")||n.startsWith("LD_")||n.startsWith("DYLD_")||Set.of("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","JDK_JAVAC_OPTIONS","_JAVA_OPTIONS","CLASSPATH","JAVA_OPTS").contains(n);});
   Process child=builder.start();boolean timeout=false;int code=-1;
   try{
    if(!child.waitFor(20,TimeUnit.SECONDS)){timeout=true;child.destroy();if(!child.waitFor(1,TimeUnit.SECONDS)){child.destroyForcibly();child.waitFor(2,TimeUnit.SECONDS);}}
    if(!child.isAlive())code=child.exitValue();
   }finally{if(child.isAlive()){child.destroyForcibly();child.waitFor(2,TimeUnit.SECONDS);}}
   JsonObject record=null;int markers=0;
   if(Files.size(log)<=1024*1024)for(String line:Files.readAllLines(log))if(line.startsWith(marker)){markers++;record=new JsonParser().parse(line.substring(marker.length())).getAsJsonObject();}
   boolean ok=code==0&&!timeout&&!child.isAlive()&&markers==1&&record.get("passed").getAsBoolean()&&name.equals(record.get("mode").getAsString());
   JsonObject item=new JsonObject();item.addProperty("case",name);item.addProperty("exit_code",code);item.addProperty("timeout",timeout);item.addProperty("child_reaped",!child.isAlive());item.addProperty("passed",ok);item.addProperty("log",log.toString());item.add("record",record);results.add(item);
   if(ok)assertions+=record.get("assertions").getAsInt();passed&=ok;if(!ok)break;
  }
  JsonObject report=new JsonObject();report.addProperty("passed",passed&&results.size()==cases.length);report.addProperty("suite",suite.getSimpleName());report.addProperty("assertions",assertions);report.addProperty("physical_qualification",false);report.add("cases",results);
  Files.writeString(root.resolve("report.json"),report+"\n",StandardOpenOption.CREATE_NEW);
  System.out.println("OPENPNP_NATIVE_COMPLETION_RESULT "+report);
  if(!report.get("passed").getAsBoolean())throw new AssertionError("Native completion suite failed; retained "+root);
 }
}
