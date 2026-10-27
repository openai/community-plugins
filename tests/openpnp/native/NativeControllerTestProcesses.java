/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;

/** Bounded own-child processes for controller reducer and crash fixtures; no configured endpoints. */
final class NativeControllerTestProcesses {
    static final Gson JSON=new Gson();
    static JsonObject object(Object...pairs){JsonObject p=new JsonObject();for(int i=0;i<pairs.length;i+=2)p.add((String)pairs[i],JSON.toJsonTree(pairs[i+1]));return p;}
    static void require(boolean value,String message){if(!value)throw new AssertionError(message);}
    static JsonObject read(Path p)throws Exception{return JSON.fromJson(Files.readString(p),JsonObject.class);}
    static void save(Path p,JsonObject value)throws Exception{Files.writeString(p,value+"\n",StandardOpenOption.CREATE_NEW);}
    static String sha(Path p)throws Exception{return sha(Files.readAllBytes(p));}
    static String sha(byte[] bytes)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format("%02x",b&255));return out.toString();}
    static JsonArray events(Path journal)throws Exception{JsonArray events=new JsonArray();for(String line:Files.readAllLines(journal))if(!line.isBlank())events.add(JSON.fromJson(line,JsonObject.class));return events;}
    static JsonObject run(Class<?> type,Path fixture,int expectedExit,String... arguments)throws Exception{
        Files.createDirectories(fixture);Path home=Files.createDirectory(fixture.resolve("home")),tmp=Files.createDirectory(fixture.resolve("tmp")),log=fixture.resolve("child.log");
        List<String> command=new ArrayList<>(Arrays.asList(Paths.get(System.getProperty("java.home"),"bin","java").toString(),"-Xmx512m","-XX:+ExitOnOutOfMemoryError","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+home,"-Djava.io.tmpdir="+tmp,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),type.getName()));Collections.addAll(command,arguments);
        ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());builder.environment().put("HOME",home.toString());
        builder.environment().keySet().removeIf(k->{String n=k.toUpperCase(Locale.ROOT);return n.startsWith("OPENPNP_")||n.startsWith("LD_")||n.startsWith("DYLD_")||Set.of("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","JDK_JAVAC_OPTIONS","_JAVA_OPTIONS","CLASSPATH","JAVA_OPTS").contains(n);});
        Process child=builder.start();Thread cleanup=new Thread(()->{if(child.isAlive()){child.destroyForcibly();try{child.waitFor(1,TimeUnit.SECONDS);}catch(InterruptedException ignored){Thread.currentThread().interrupt();}}},"controller-fixture-child-cleanup");Runtime.getRuntime().addShutdownHook(cleanup);
        boolean timeout=false;long start=System.nanoTime();
        try{if(!child.waitFor(15,TimeUnit.SECONDS)){timeout=true;child.destroy();if(!child.waitFor(1,TimeUnit.SECONDS)){child.destroyForcibly();child.waitFor(2,TimeUnit.SECONDS);}}}
        finally{if(child.isAlive()){child.destroyForcibly();child.waitFor(2,TimeUnit.SECONDS);}Runtime.getRuntime().removeShutdownHook(cleanup);}
        JsonObject receipt=object("class",type.getName(),"command",command,"expected_exit_code",expectedExit,"exit_code",child.isAlive()?-1:child.exitValue(),"timeout",timeout,"child_reaped",!child.isAlive(),"elapsed_ms",TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-start),"log",log.toString(),"log_sha256",sha(log));save(fixture.resolve("process.json"),receipt);
        require(!timeout&&!child.isAlive()&&child.exitValue()==expectedExit,"Controller child failed; retained "+fixture+" (expected exit "+expectedExit+")");return receipt;
    }
}
