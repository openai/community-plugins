/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.*;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Private restart-persistent receipts for self-generated native documents, never an XML importer.
 * The owning Bridge must hold its exclusive journal lock while using this store. */
final class NativeDocumentStore {
    static final int MAX_DOCUMENTS=16, MAX_ARCHIVE_BYTES=8*1024*1024, MAX_RECEIPT_BYTES=512*1024;
    static final long MAX_ARCHIVES_BYTES=64L*1024*1024, MAX_STORE_BYTES=384L*1024*1024;
    private static final Gson GSON=new Gson();
    final Path root;
    private final byte[] key;
    @FunctionalInterface interface PublicationProbe {void reached(Path target,boolean renamed)throws Exception;}
    private final PublicationProbe publicationProbe;
    private final Map<String,Record> records=new LinkedHashMap<>();

    static final class Record {
        final String id;
        final Path file;
        final LinkedHashMap<String,String> hashes,parts,packages;
        Record(String id,Path file,LinkedHashMap<String,String> hashes,LinkedHashMap<String,String> parts,LinkedHashMap<String,String> packages){this.id=id;this.file=file;this.hashes=hashes;this.parts=parts;this.packages=packages;}
    }
    NativeDocumentStore(Path root)throws Exception {this(root,(target,renamed)->{});}
    /** Package-private deterministic process-crash seam; never selected by a tool or environment. */
    NativeDocumentStore(Path root,PublicationProbe publicationProbe)throws Exception {
        this.root=root.toRealPath();
        this.publicationProbe=Objects.requireNonNull(publicationProbe);
        checkBudget();
        Path keyPath=root.resolve("receipt-key");
        if(!Files.exists(keyPath,LinkOption.NOFOLLOW_LINKS)){
            try(java.util.stream.Stream<Path> paths=Files.list(root)){
                if(paths.anyMatch(p->p.getFileName().toString().endsWith(".receipt.json")))fail("DOCUMENT_RECEIPT_KEY_MISSING","Committed document receipts exist without their private key");
            }
            byte[] generated=new byte[32];new SecureRandom().nextBytes(generated);publish(keyPath,generated);
        }
        java.nio.file.attribute.PosixFileAttributes keyAttributes=Files.readAttributes(keyPath,java.nio.file.attribute.PosixFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
        if(!keyAttributes.owner().equals(Files.getOwner(root))||!keyAttributes.permissions().equals(PosixFilePermissions.fromString("rw-------")))fail("DOCUMENT_RECEIPT_KEY_INVALID","Native document receipt key must be owned by the store owner with mode 0600");
        key=read(keyPath,32);if(key.length!=32)fail("DOCUMENT_RECEIPT_KEY_INVALID","Expected a 32-byte document receipt key");
        List<Path> receipts=new ArrayList<>();try(java.util.stream.Stream<Path> paths=Files.list(root)){paths.filter(p->p.getFileName().toString().endsWith(".receipt.json")).forEach(receipts::add);}
        if(receipts.size()>MAX_DOCUMENTS)fail("DOCUMENT_CAPACITY","Too many retained native document receipts");
        receipts.sort(Comparator.comparing(p->p.getFileName().toString()));
        for(Path receipt:receipts){Record record=verifyReceipt(receipt);records.put(record.id,record);}
    }
    Map<String,Record> records(){return Collections.unmodifiableMap(records);}

    synchronized void save(String id,byte[] archive,Map<String,String> hashes,Map<String,String> parts,Map<String,String> packages)throws Exception {
        if(!id.matches("[a-f0-9]{64}")||!id.equals(sha(archive)))fail("ARTIFACT_INTEGRITY","Native archive does not match its content address");
        if(archive.length>MAX_ARCHIVE_BYTES)fail("DOCUMENT_TOO_LARGE","Native archive exceeds eight MiB");
        checkBudget();
        Path archivePath=root.resolve(id+".zip"),receiptPath=root.resolve(id+".receipt.json");
        if(!records.containsKey(id)&&records.size()>=MAX_DOCUMENTS)fail("DOCUMENT_CAPACITY","Retained native document receipt limit reached");
        if(records.containsKey(id)||Files.exists(receiptPath,LinkOption.NOFOLLOW_LINKS)){
            // A receipt rename can succeed before publish returns or the live cache is updated.
            // Adopt only the caller's exact authenticated artifact/dependency facts. Establish
            // the directory durability boundary, then reverify before making it loadable.
            verifyExpectedReceipt(receiptPath,id,hashes,parts,packages);
            forceDirectory();
            Record record=verifyExpectedReceipt(receiptPath,id,hashes,parts,packages);
            records.put(id,record);return;
        }
        if(Files.exists(archivePath,LinkOption.NOFOLLOW_LINKS)){
            if(!sha(read(archivePath,MAX_ARCHIVE_BYTES)).equals(id))fail("ARTIFACT_INTEGRITY","An incomplete archive with this identity has different bytes");
        }else{
            if(archiveBytes()+archive.length>MAX_ARCHIVES_BYTES)fail("DOCUMENT_CAPACITY","Retained archive byte budget reached");
            publish(archivePath,archive);
        }
        Map<String,Object> body=Bridge.map("version",1,"upstream_commit",Bridge.UPSTREAM,"sha256",id,"archive_bytes",archive.length,"entry_hashes",hashes,"parts",parts,"packages",packages);
        String payload=GSON.toJson(body);
        byte[] receipt=GSON.toJson(Bridge.map("payload",payload,"hmac_sha256",hmac(payload))).getBytes(StandardCharsets.UTF_8);
        if(receipt.length>MAX_RECEIPT_BYTES)fail("DOCUMENT_TOO_LARGE","Native document receipt exceeds its byte limit");
        publish(receiptPath,receipt);Record record=verifyReceipt(receiptPath);records.put(id,record);
    }
    Record verify(String id)throws Exception {
        if(!records.containsKey(id))fail("DOCUMENT_NOT_FOUND","Only committed self-generated native documents can be loaded");
        return verifyReceipt(root.resolve(id+".receipt.json"));
    }
    private Record verifyExpectedReceipt(Path path,String id,Map<String,String> hashes,Map<String,String> parts,Map<String,String> packages)throws Exception {
        Record record=verifyReceipt(path);
        if(!id.equals(record.id)||!record.hashes.equals(hashes)||!record.parts.equals(parts)||!record.packages.equals(packages))
            fail("DOCUMENT_STORE_CONFLICT","Existing native document receipt differs from the requested artifact or dependencies");
        return record;
    }
    private Record verifyReceipt(Path path)throws Exception {
        String filename=path.getFileName().toString();
        if(!filename.matches("[a-f0-9]{64}\\.receipt\\.json"))fail("DOCUMENT_RECEIPT_INVALID","Invalid native document receipt name");
        String id=filename.substring(0,64);
        try {
            JsonObject wrapper=new JsonParser().parse(new String(read(path,MAX_RECEIPT_BYTES),StandardCharsets.UTF_8)).getAsJsonObject();
            only(wrapper,"payload","hmac_sha256");String payload=string(wrapper,"payload"),signature=string(wrapper,"hmac_sha256");
            if(!signature.matches("[a-f0-9]{64}")||!MessageDigest.isEqual(signature.getBytes(StandardCharsets.US_ASCII),hmac(payload).getBytes(StandardCharsets.US_ASCII)))fail("DOCUMENT_RECEIPT_INVALID","Native document receipt signature mismatch");
            JsonObject value=new JsonParser().parse(payload).getAsJsonObject();only(value,"version","upstream_commit","sha256","archive_bytes","entry_hashes","parts","packages");
            if(!"1".equals(value.get("version").toString())||!Bridge.UPSTREAM.equals(string(value,"upstream_commit"))||!id.equals(string(value,"sha256")))fail("DOCUMENT_RECEIPT_INVALID","Native document receipt version, build or identity mismatch");
            LinkedHashMap<String,String> hashes=hashMap(value.getAsJsonObject("entry_hashes"),1002,true),parts=hashMap(value.getAsJsonObject("parts"),10000,false),packages=hashMap(value.getAsJsonObject("packages"),10000,false);
            if(!hashes.containsKey("manifest.json")||!hashes.containsKey("job.job.xml"))fail("DOCUMENT_RECEIPT_INVALID","Native document receipt is missing its entrypoint or manifest");
            Path archive=root.resolve(id+".zip");byte[] bytes=read(archive,MAX_ARCHIVE_BYTES);
            if(!Integer.toString(bytes.length).equals(value.get("archive_bytes").toString())||!sha(bytes).equals(id))fail("ARTIFACT_INTEGRITY","Committed native archive does not match its receipt");
            return new Record(id,archive,hashes,parts,packages);
        }catch(Bridge.Fault error){throw error;}catch(Exception error){throw new Bridge.Fault("DOCUMENT_RECEIPT_INVALID","Cannot verify the committed native document receipt");}
    }
    private static LinkedHashMap<String,String> hashMap(JsonObject object,int maximum,boolean entries)throws Exception {
        if(object==null||object.entrySet().size()>maximum)fail("DOCUMENT_RECEIPT_INVALID","Native receipt map exceeds its limit");
        LinkedHashMap<String,String> result=new LinkedHashMap<>();
        for(Map.Entry<String,JsonElement> entry:object.entrySet()){
            String name=entry.getKey(),hash=string(object,name);
            if(name.isEmpty()||name.length()>1024||!hash.matches("[a-f0-9]{64}"))fail("DOCUMENT_RECEIPT_INVALID","Invalid native receipt map entry");
            if(entries&&!name.matches("(?:job\\.job\\.xml|manifest\\.json|asset-[0-9]{4}\\.(?:board|panel)\\.xml)"))fail("DOCUMENT_RECEIPT_INVALID","Receipt contains a non-generated native asset name");
            result.put(name,hash);
        }
        return result;
    }
    void checkBudget()throws Exception {requireCapacity(0);}
    void requireCapacity(long additionalBytes)throws Exception {
        if(additionalBytes<0||additionalBytes>MAX_STORE_BYTES)fail("DOCUMENT_CAPACITY","Invalid native document storage reservation");
        long bytes=0;int files=0;
        try(java.util.stream.Stream<Path> paths=Files.walk(root)){
            Iterator<Path> iterator=paths.iterator();while(iterator.hasNext()){
                Path path=iterator.next();if(Files.isSymbolicLink(path))fail("PATH_REJECTED","Native document store does not follow symbolic links");
                if(Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)){long size=Files.size(path);if(++files>100000||size>MAX_STORE_BYTES-bytes-additionalBytes)fail("DOCUMENT_CAPACITY","Native document store disk budget reached");bytes+=size;}
                else if(!Files.isDirectory(path,LinkOption.NOFOLLOW_LINKS))fail("PATH_REJECTED","Native document store contains an unsupported file type");
            }
        }
        if(archiveBytes()>MAX_ARCHIVES_BYTES)fail("DOCUMENT_CAPACITY","Retained native archive byte budget reached");
    }
    private long archiveBytes()throws Exception {
        long total=0;try(java.util.stream.Stream<Path> paths=Files.list(root)){Iterator<Path> iterator=paths.iterator();while(iterator.hasNext()){Path p=iterator.next();if(p.getFileName().toString().endsWith(".zip")){if(!Files.isRegularFile(p,LinkOption.NOFOLLOW_LINKS))fail("PATH_REJECTED","Expected regular native archive");long size=Files.size(p);if(size>MAX_ARCHIVES_BYTES-total)fail("DOCUMENT_CAPACITY","Retained native archive byte budget reached");total+=size;}}}return total;
    }
    private String hmac(String payload)throws Exception{Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(key,"HmacSHA256"));return hex(mac.doFinal(payload.getBytes(StandardCharsets.UTF_8)));}
    private static String string(JsonObject object,String key)throws Exception{JsonElement value=object.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString())fail("DOCUMENT_RECEIPT_INVALID","Expected a string receipt field");return value.getAsString();}
    private static void only(JsonObject object,String...keys)throws Exception{Set<String> observed=new HashSet<>();for(Map.Entry<String,JsonElement> entry:object.entrySet())observed.add(entry.getKey());if(!observed.equals(new HashSet<>(Arrays.asList(keys))))fail("DOCUMENT_RECEIPT_INVALID","Unexpected native receipt fields");}
    private static byte[] read(Path path,int maximum)throws Exception{if(!Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)||Files.size(path)>maximum)fail("DOCUMENT_RECEIPT_INVALID","Expected a bounded regular native document store file");try(InputStream in=Files.newInputStream(path)){byte[] bytes=in.readNBytes(maximum+1);if(bytes.length>maximum)fail("DOCUMENT_RECEIPT_INVALID","Native document store file grew beyond its byte limit");return bytes;}}
    private void publish(Path path,byte[] bytes)throws Exception {
        if(Files.exists(path,LinkOption.NOFOLLOW_LINKS))fail("DOCUMENT_STORE_CONFLICT","Immutable native document store file already exists");
        Path pending=Files.createTempFile(path.getParent(),".pending-","",PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        try(FileChannel channel=FileChannel.open(pending,StandardOpenOption.WRITE)){ByteBuffer buffer=ByteBuffer.wrap(bytes);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);}
        publicationProbe.reached(path,false);
        Files.move(pending,path,StandardCopyOption.ATOMIC_MOVE);
        publicationProbe.reached(path,true);
        forceDirectory();
    }
    private void forceDirectory()throws IOException {try(FileChannel directory=FileChannel.open(root,StandardOpenOption.READ)){directory.force(true);}}
    private static String sha(byte[] bytes)throws Exception{return hex(MessageDigest.getInstance("SHA-256").digest(bytes));}
    private static String hex(byte[] bytes){StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format(Locale.ROOT,"%02x",b));return s.toString();}
    private static void fail(String code,String message)throws Bridge.Fault{throw new Bridge.Fault(code,message);}
}
