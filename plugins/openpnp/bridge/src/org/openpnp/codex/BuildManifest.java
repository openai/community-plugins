/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.Gson;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.stream.Stream;
/** Generates content receipts without an additional build-time scripting runtime. */
public final class BuildManifest {
    public static void main(String[]args)throws Exception {
        Path bridgeDir=Paths.get(args[0]);Path distribution=Paths.get(args[1]);
        Files.writeString(bridgeDir.resolve("build-manifest.json"),new Gson().toJson(Bridge.map("upstream_commit",Bridge.UPSTREAM,"bridge_version","0.1.0","bridge_sha256",hash(bridgeDir.resolve("openpnp-codex-bridge.jar"))))) ;
        List<Map<String,Object>> files=new ArrayList<>();
        try(Stream<Path> stream=Files.walk(distribution)){for(Path path:(Iterable<Path>)stream.filter(Files::isRegularFile).sorted()::iterator){String name=distribution.relativize(path).toString().replace('\\','/');if(!name.equals("codex-build-manifest.json"))files.add(Bridge.map("path",name,"sha256",hash(path)));}}
        Files.writeString(distribution.resolve("codex-build-manifest.json"),new Gson().toJson(Bridge.map("upstream_commit",Bridge.UPSTREAM,"gui_jar","openpnp-gui-0.0.1-alpha-SNAPSHOT.jar","libs_directory","lib","samples_directory","samples","files",files)));
    }
    private static String hash(Path path)throws Exception{MessageDigest digest=MessageDigest.getInstance("SHA-256");try(java.io.InputStream in=Files.newInputStream(path)){byte[] block=new byte[65536];int n;while((n=in.read(block))!=-1)digest.update(block,0,n);}StringBuilder out=new StringBuilder();for(byte b:digest.digest())out.append(String.format("%02x",b));return out.toString();}
}
