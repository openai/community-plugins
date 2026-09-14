/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.io.*;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.util.*;
import java.util.jar.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.Configuration;

/** Preserve only byte-exact generated native examples, outside the executable script tree. */
final class GuiExampleScripts {
    static Map<String,Object> quarantine(Configuration config,Path state)throws Exception {
        Path scripts=config.getScripting().getScriptsDirectory().toPath().toRealPath(),examples=scripts.resolve("Examples");
        if(!Files.exists(examples,LinkOption.NOFOLLOW_LINKS))return Bridge.map("state","absent","source","verified-native-jar","files_moved",0);
        if(Files.isSymbolicLink(examples)||!Files.isDirectory(examples,LinkOption.NOFOLLOW_LINKS))throw new IOException("Native Examples must be a regular directory");
        Map<String,byte[]> expected=new TreeMap<>();Set<String> expectedDirectories=new HashSet<>(Arrays.asList("JavaScript","Python"));
        try(JarFile jar=new JarFile(GuiBootstrap.codeJar(MainFrame.class).toFile())){
            Enumeration<JarEntry> entries=jar.entries();long total=0;
            while(entries.hasMoreElements()){JarEntry entry=entries.nextElement();String prefix="scripts/Examples/";
                if(entry.isDirectory()||!entry.getName().startsWith(prefix))continue;String name=entry.getName().substring(prefix.length());
                if(entry.getSize()<0||entry.getSize()>1024*1024||name.contains("..")||name.startsWith("/")||expected.size()>=64)throw new IOException("Native example resource inventory exceeds limit");
                byte[] bytes;try(InputStream in=jar.getInputStream(entry)){bytes=in.readNBytes(1024*1024+1);}total+=bytes.length;if(bytes.length>1024*1024||total>4*1024*1024)throw new IOException("Native example resources exceed size limit");expected.put(name,bytes);
            }
        }
        if(expected.size()!=16)throw new IOException("Pinned native example inventory differs from expected16files");
        Set<String> observed=new HashSet<>();try(java.util.stream.Stream<Path> files=Files.walk(examples)){
            for(Path file:(Iterable<Path>)files::iterator){if(file.equals(examples))continue;String name=examples.relativize(file).toString().replace(File.separatorChar,'/');
                if(Files.isSymbolicLink(file))throw new IOException("Native example symlinks are refused");
                if(Files.isDirectory(file,LinkOption.NOFOLLOW_LINKS)){if(!expectedDirectories.contains(name))throw new IOException("Unknown directory in native examples; preserve and review locally");continue;}
                byte[] nativeBytes=expected.get(name);if(nativeBytes==null||!Files.isRegularFile(file,LinkOption.NOFOLLOW_LINKS)||Files.size(file)!=nativeBytes.length||!Arrays.equals(Files.readAllBytes(file),nativeBytes))throw new IOException("Modified or unknown native example; preserve and review locally: "+name);observed.add(name);
            }
        }
        if(!observed.equals(expected.keySet()))throw new IOException("Incomplete native example inventory; preserve and review locally");
        long archives;try(java.util.stream.Stream<Path> children=Files.list(state)){archives=children.filter(p->p.getFileName().toString().startsWith("native-examples-")).count();}
        if(archives>=32)throw new IOException("Native example archive limit32 reached; review retained copies locally");
        Path archive=Files.createTempDirectory(state,"native-examples-");Path destination=archive.resolve("Examples");
        try{Files.move(examples,destination,StandardCopyOption.ATOMIC_MOVE);}catch(Exception error){Files.deleteIfExists(archive);throw error;}
        for(Path directory:Arrays.asList(scripts,archive,state))try(FileChannel channel=FileChannel.open(directory,StandardOpenOption.READ)){channel.force(true);}
        Map<String,Object> hashes=new TreeMap<>();for(Map.Entry<String,byte[]> file:expected.entrySet())hashes.put(file.getKey(),GuiBootstrap.hash(file.getValue()));
        return Bridge.map("state","preserved-outside-script-tree","source","verified-native-jar","files_moved",expected.size(),"archive_directory",state.relativize(destination).toString(),"sha256_by_relative_path",hashes);
    }
}
