/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.zip.*;

/** Real private store writes, HMAC verification, file force/rename, and same-process retries.
 * Synthetic ZIP contents; no native Job reconstruction, Bridge recovery, or power-loss claim. */
public final class NativeDocumentStoreRetryTest {
    static int checks,refusals; static final List<Object> cases=new ArrayList<>();
    interface Work {void run()throws Exception;}
    static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
    static void reject(String code,Work work)throws Exception {
        try{work.run();throw new AssertionError("Accepted expected refusal: "+code);}
        catch(Bridge.Fault failure){check(code.equals(failure.code),"Exact refusal "+code+" got "+failure.code);refusals++;}
    }
    static String sha(byte[] bytes)throws Exception{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));}
    static final class Data {
        final byte[] archive;final String id;final Map<String,String> hashes=new LinkedHashMap<>(),parts=new LinkedHashMap<>(),packages=new LinkedHashMap<>();
        Data(String name)throws Exception {
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();
            try(ZipOutputStream zip=new ZipOutputStream(bytes)){
                for(String entry:List.of("job.job.xml","manifest.json")){
                    byte[] content=(name+":"+entry).getBytes(StandardCharsets.UTF_8);hashes.put(entry,sha(content));
                    ZipEntry item=new ZipEntry(entry);item.setTime(0);zip.putNextEntry(item);zip.write(content);zip.closeEntry();
                }
            }
            archive=bytes.toByteArray();id=sha(archive);parts.put("test-part",sha("part".getBytes(StandardCharsets.UTF_8)));packages.put("test-package",sha("package".getBytes(StandardCharsets.UTF_8)));
        }
        void save(NativeDocumentStore store)throws Exception{store.save(id,archive,hashes,parts,packages);}
    }
    static Path archive(Path root,Data data){return root.resolve(data.id+".zip");}
    static Path receipt(Path root,Data data){return root.resolve(data.id+".receipt.json");}
    static Map<String,Object> files(Path root)throws Exception {
        Map<String,Object> result=new TreeMap<>();try(var paths=Files.list(root)){for(Path p:paths.toList()){
            BasicFileAttributes a=Files.readAttributes(p,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
            result.put(p.getFileName().toString(),List.of(String.valueOf(a.fileKey()),a.size(),a.lastModifiedTime().toString(),a.isSymbolicLink()?Files.readSymbolicLink(p).toString():sha(Files.readAllBytes(p))));
        }}return result;
    }
    static NativeDocumentStore interrupted(Path root,Data data,String stage,AtomicInteger observations)throws Exception {
        AtomicBoolean once=new AtomicBoolean();NativeDocumentStore store=new NativeDocumentStore(root,(target,renamed)->{
            observations.incrementAndGet();String kind=target.getFileName().toString().endsWith(".receipt.json")?"receipt":target.getFileName().toString().endsWith(".zip")?"archive":"key";
            if(stage.equals(kind+(renamed?"-after":"-before"))&&once.compareAndSet(false,true))throw new IOException("one-shot "+stage+" interruption");
        });
        try{data.save(store);throw new AssertionError("No actual publication interruption");}
        catch(IOException expected){check(expected.getMessage().equals("one-shot "+stage+" interruption"),"Actual selected publication seam threw");}
        check(store.records().isEmpty(),"Unreturned publication was not inserted into live record cache");
        reject("DOCUMENT_NOT_FOUND",()->store.verify(data.id));return store;
    }
    static void retry(Path root,String stage)throws Exception {
        int begin=checks;Files.createDirectory(root);Data data=new Data(stage);AtomicInteger calls=new AtomicInteger();NativeDocumentStore store=interrupted(root,data,stage,calls);
        boolean hadReceipt=Files.exists(receipt(root,data)),hadArchive=Files.exists(archive(root,data));Map<String,Object> before=files(root);int beforeCalls=calls.get();
        data.save(store);NativeDocumentStore.Record verified=store.verify(data.id);
        check(verified.id.equals(data.id)&&verified.hashes.equals(data.hashes)&&verified.parts.equals(data.parts)&&verified.packages.equals(data.packages),"Retry retains exact requested artifact and dependency facts");
        Map<String,Object> after=files(root);
        if(hadReceipt){check(before.equals(after),"Existing receipt adoption rewrites no file, inode, bytes, timestamps, or pending file");check(beforeCalls==calls.get(),"Receipt adoption performs no new rename publication");}
        if(hadArchive)check(before.get(data.id+".zip").equals(after.get(data.id+".zip")),"Retry never rewrites existing archive");
        for(var entry:before.entrySet())check(entry.getValue().equals(after.get(entry.getKey())),"Uncommitted original files remain exact: "+entry.getKey());
        Map<String,Object> committed=files(root);data.save(store);check(committed.equals(files(root)),"Completed same-content retry rewrites no file");
        NativeDocumentStore reopened=new NativeDocumentStore(root);check(reopened.records().size()==1,"New store imports the complete signed receipt");data.save(reopened);check(committed.equals(files(root)),"Reopened same-content retry preserves all bytes and identities");
        cases.add(Bridge.map("case",stage,"checks",checks-begin));
    }
    static void changedMaps(Path root,boolean interrupted)throws Exception {
        int begin=checks;Files.createDirectory(root);Data data=new Data(root.getFileName().toString());NativeDocumentStore store;
        if(interrupted)store=interrupted(root,data,"receipt-after",new AtomicInteger());else{store=new NativeDocumentStore(root);data.save(store);}
        Map<String,Object> before=files(root);final NativeDocumentStore selected=store;
        for(String field:List.of("entries","parts","packages")){
            Map<String,String> hashes=new LinkedHashMap<>(data.hashes),parts=new LinkedHashMap<>(data.parts),packages=new LinkedHashMap<>(data.packages);
            Map<String,String> changed=field.equals("entries")?hashes:field.equals("parts")?parts:packages;changed.replaceAll((key,value)->"f".repeat(64));
            reject("DOCUMENT_STORE_CONFLICT",()->selected.save(data.id,data.archive,hashes,parts,packages));check(before.equals(files(root)),"Foreign requested "+field+" refuses without overwriting existing files");
        }
        data.save(store);check(before.equals(files(root)),"Exact facts still adopt after foreign-map refusal");cases.add(Bridge.map("case",interrupted?"pending-foreign-maps":"committed-foreign-maps","checks",checks-begin));
    }
    static void corrupt(Path root,String kind)throws Exception {
        int begin=checks;Files.createDirectory(root);Data data=new Data(kind);NativeDocumentStore store=interrupted(root,data,"receipt-after",new AtomicInteger());
        Path target=kind.equals("archive")?archive(root,data):receipt(root,data);
        if(kind.equals("symlink")){Path copy=root.resolve("preserved-copy");Files.move(target,copy);Files.createSymbolicLink(target,copy);}
        else {byte[] bytes=Files.readAllBytes(target);bytes[bytes.length/2]^=1;Files.write(target,bytes);}
        Map<String,Object> before=files(root);reject(kind.equals("archive")?"ARTIFACT_INTEGRITY":kind.equals("symlink")?"PATH_REJECTED":"DOCUMENT_RECEIPT_INVALID",()->data.save(store));
        check(store.records().isEmpty(),"Rejected existing receipt does not enter live cache");check(before.equals(files(root)),"Corrupt or aliased receipt remains untouched");cases.add(Bridge.map("case","corrupt-"+kind,"checks",checks-begin));
    }
    public static void main(String[] args)throws Exception {
        Path root=Path.of(args[0]);Files.createDirectory(root);Throwable error=null;
        try{
            for(String stage:List.of("receipt-after","archive-after","receipt-before","archive-before"))retry(root.resolve(stage),stage);
            changedMaps(root.resolve("pending-maps"),true);changedMaps(root.resolve("committed-maps"),false);
            for(String kind:List.of("receipt","archive","symlink"))corrupt(root.resolve("corrupt-"+kind),kind);
        }catch(Throwable failure){error=failure;failure.printStackTrace();}
        Map<String,Object> result=Bridge.map("passed",error==null,"checks",checks,"refusals",refusals,"cases",cases,"error",error==null?null:error.toString(),"actual_private_store_io",true,"synthetic_zip_contents",true,"native_machine",false,"bridge_continuation",false,"host_power_loss_qualified",false);
        Files.writeString(root.resolve("proof.json"),new com.google.gson.GsonBuilder().setPrettyPrinting().serializeNulls().create().toJson(result)+"\n",StandardOpenOption.CREATE_NEW);
        System.out.println("NATIVE_DOCUMENT_STORE_RETRY "+new com.google.gson.Gson().toJson(result));if(error!=null)System.exit(1);
    }
}
