/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.awt.geom.Area;
import java.awt.geom.PathIterator;
import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.openpnp.model.*;

/**
 * Saves trusted in-memory native jobs and linked native definitions to self-generated documents.
 * This is NOT an arbitrary XML importer: reload accepts only signed receipts in the owned store.
 * The bridge owns simulator identity, exclusive executor, revision/fault fences and external UUIDs.
 * Native save updates enabled/error-handling maps; file/reference/dirty metadata is restored finally.
 * Native transient vision-registration transforms do not survive native serialization.
 */
public final class NativeJobDocuments {
    private static final int MAX_BYTES=8*1024*1024, MAX_DOCUMENTS=16, MAX_ASSETS=1000, MAX_LOCATIONS=5000, MAX_PLACEMENTS=100000, MAX_EXPANDED_PLACEMENTS=10000, MAX_RELOADS=32;
    private static final long MAX_RETAINED_BYTES=64L*1024*1024;
    private static final Gson GSON=new Gson();
    private final Configuration config;
    private final Path root;
    private final NativeDocumentStore store;
    private final Map<String,Receipt> retained=new LinkedHashMap<>();
    private long retainedBytes;
    private int reloads;

    /** Caller-supplied association only. The owning journal must independently match it.
     * Historical versions are loadable; this value grants no structural or machine authority. */
    public static final class LineageMetadata {
        public final String lineageId;
        public final long lineageRevision;
        public LineageMetadata(String lineageId,long lineageRevision)throws Bridge.Fault {
            if(lineageId==null||!lineageId.matches("[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")||lineageRevision<1||lineageRevision>9007199254740991L)
                fail("DOCUMENT_LINEAGE_INVALID","Lineage requires a canonical lowercase UUID and integer revision from 1 through 2^53-1");
            this.lineageId=lineageId;this.lineageRevision=lineageRevision;
        }
        private Map<String,Object> manifest(){return values("version",1,"lineage_id",lineageId,"lineage_revision",lineageRevision);}
    }
    public static final class Reloaded {
        public final Job job;
        public final Optional<LineageMetadata> lineage;
        private Reloaded(Job job,LineageMetadata lineage){this.job=job;this.lineage=Optional.ofNullable(lineage);}
    }

    public static final class Saved {
        public final String sha256;
        public final byte[] bytes;
        public final Map<String,Object> manifest;
        private Saved(String sha,byte[] bytes,Map<String,Object> manifest){this.sha256=sha;this.bytes=bytes;this.manifest=manifest;}
    }
    private static final class Receipt {
        final Path file;
        final LinkedHashMap<String,String> hashes;
        final LinkedHashMap<String,String> parts,packages;
        Receipt(Path file,LinkedHashMap<String,String> hashes,LinkedHashMap<String,String> parts,LinkedHashMap<String,String> packages){this.file=file;this.hashes=new LinkedHashMap<>(hashes);this.parts=new LinkedHashMap<>(parts);this.packages=new LinkedHashMap<>(packages);}
    }

    public NativeJobDocuments(Configuration config,Path generatedRoot)throws Exception {
        this(config,generatedRoot,config.getConfigurationDirectory().toPath());
    }

    /** The Bridge may use its exclusively locked persistent journal directory as the allowed root. */
    public NativeJobDocuments(Configuration config,Path generatedRoot,Path allowedRoot)throws Exception {
        this.config=Objects.requireNonNull(config);
        Path configuredPath=allowedRoot.toAbsolutePath().normalize();
        Path configurationRoot=configuredPath.toRealPath();
        Path candidate=generatedRoot.toAbsolutePath().normalize();
        if(candidate.startsWith(configuredPath))candidate=configurationRoot.resolve(configuredPath.relativize(candidate));
        if(!candidate.startsWith(configurationRoot)||candidate.equals(configurationRoot))fail("PATH_REJECTED","Document root must be a generated child of the explicitly owned storage root");
        for(Path p=candidate;p!=null&&p.startsWith(configurationRoot);p=p.getParent())if(Files.isSymbolicLink(p))fail("PATH_REJECTED","Document staging does not follow symbolic links");
        Files.createDirectories(candidate,java.nio.file.attribute.PosixFilePermissions.asFileAttribute(java.nio.file.attribute.PosixFilePermissions.fromString("rwx------")));root=candidate.toRealPath();
        store=new NativeDocumentStore(root);
        for(NativeDocumentStore.Record record:store.records().values()){retained.put(record.id,new Receipt(record.file,record.hashes,record.parts,record.packages));retainedBytes+=Files.size(record.file);}
        try(java.util.stream.Stream<Path> paths=Files.list(root)){reloads=(int)paths.filter(p->p.getFileName().toString().startsWith("load-")).count();}
    }

    /** Read-only detached graph validation, without save/load or document-store mutation. */
    static void validateCandidate(Job job)throws Exception { new Graph(job); }

    /** Run exclusively on the native executor; does not install another job or touch machine motion. */
    public synchronized Saved save(Job job)throws Exception {
        return save(job,null);
    }

    /** Optional trusted caller association, covered by archive/entry hashes and the owned receipt.
     * Null intentionally preserves unknown lineage, including old callers and old documents. */
    public synchronized Saved save(Job job,LineageMetadata lineage)throws Exception {
        store.requireCapacity(2L*MAX_BYTES+NativeDocumentStore.MAX_RECEIPT_BYTES);
        if(retained.size()>=MAX_DOCUMENTS)fail("DOCUMENT_CAPACITY","Retained native document limit reached");
        Graph graph=new Graph(job); // Validate the complete object graph before any native file rebinding.
        LinkedHashMap<String,String> partHashes=new LinkedHashMap<>(),packageHashes=new LinkedHashMap<>();
        for(Part part:graph.parts){if(config.getPart(part.getId())!=part)fail("PART_CONFLICT","Job part is not the configured native identity: "+part.getId());partHashes.put(part.getId(),fingerprint(part));org.openpnp.model.Package pkg=part.getPackage();if(pkg==null||config.getPackage(pkg.getId())!=pkg)fail("PACKAGE_UNMAPPED","Job requires a configured package: "+part.getId());packageHashes.put(pkg.getId(),fingerprint(pkg));}
        Path staging=Files.createTempDirectory(root,"save-");
        LinkedHashMap<String,byte[]> files=new LinkedHashMap<>();
        File jobFile=job.getFile();boolean jobDirty=job.isDirty();
        List<Runnable> restoreFiles=new ArrayList<>(),restoreDirty=new ArrayList<>();
        try {
            Panel rootPanel=job.getRootPanelLocation().getPanel();boolean rootDirty=rootPanel.isDirty();restoreDirty.add(()->rootPanel.setDirty(rootDirty));
            for(PlacementsHolderLocation<?> location:job.getRootPanelLocation().getChildren()){String old=location.getFileName();boolean dirty=location.isDirty();restoreFiles.add(()->location.setFileName(old));restoreDirty.add(()->location.setDirty(dirty));location.setFileName(graph.names.get(location.getPlacementsHolder().getDefinition()));}
            // Clone only our own serialized native definitions. Original shared files/listeners are
            // untouched; native copy constructors omit some fields, so serialization is intentional.
            for(PlacementsHolder<?> definition:graph.definitions) {
                PlacementsHolder<?> copy=copyDefinition(definition);
                if(copy.getClass()==Panel.class){Panel panel=(Panel)copy,sourcePanel=(Panel)definition;List<PlacementsHolderLocation<?>> children=panel.getChildren(),source=sourcePanel.getChildren();for(int i=0;i<children.size();i++)children.get(i).setFileName(graph.names.get(source.get(i).getPlacementsHolder().getDefinition()));
                    // Panel.persist rebuilds IDs from transient pseudoPlacements. A serializer clone
                    // has IDs but no resolved pseudo objects yet; retain the exact IDs for native load.
                    for(Placement pseudo:sourcePanel.getPseudoPlacements())panel.addPseudoPlacement(new Placement(pseudo.getId()));}
                copy.setFile(staging.resolve(graph.names.get(definition)).toFile());
                if(copy.getClass()==Board.class)config.saveBoard((Board)copy);else config.savePanel((Panel)copy);
            }
            // Upstream save clears enabled/error overrides but leaves old fiducial-check overrides.
            // Clear this corresponding cache too, so a true -> definition-default false change saves.
            job.removeAllCheckFiducialsState();
            config.saveJob(job,staging.resolve("job.job.xml").toFile());
            int bytes=0;
            for(String name:graph.names.values()){byte[] content=readBounded(staging.resolve(name));bytes+=content.length;if(bytes>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Native assets exceed the document byte limit");files.put(name,content);}
            byte[] content=readBounded(staging.resolve("job.job.xml"));if((long)bytes+content.length>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Native job exceeds the document byte limit");files.put("job.job.xml",content);
        } finally {
            // Reference listeners may react to file changes; restore dirty flags after every file/ref.
            for(Runnable restore:restoreFiles)restore.run();for(Runnable restore:restoreDirty)restore.run();job.setFile(jobFile);job.setDirty(jobDirty);
            deleteTree(staging);
        }
        List<Map<String,Object>> entries=new ArrayList<>();LinkedHashMap<String,String> hashes=new LinkedHashMap<>();
        for(Map.Entry<String,byte[]> entry:files.entrySet()){String hash=sha(entry.getValue());hashes.put(entry.getKey(),hash);entries.add(values("name",entry.getKey(),"sha256",hash,"bytes",entry.getValue().length));}
        Map<String,Object> manifest=values("format","openpnp-native-job-document","version",1,"upstream_commit",Bridge.UPSTREAM,"entrypoint","job.job.xml","entries",entries,"required_parts",partHashes,"required_packages",packageHashes,"shared_definitions_preserved",true,"placed_history_preserved",true,"custom_outlines_supported",false,"transient_registration_preserved",false,"portable_machine_backup",false,"reload_scope","same-owned-document-store-and-unchanged-part-package-definitions","physical_qualification",false);
        if(lineage!=null)manifest.put("lineage",lineage.manifest());
        byte[] manifestBytes=GSON.toJson(manifest).getBytes(java.nio.charset.StandardCharsets.UTF_8);files.put("manifest.json",manifestBytes);hashes.put("manifest.json",sha(manifestBytes));
        long total=0;for(byte[] bytes:files.values())total+=bytes.length;if(total>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Uncompressed native document exceeds 8 MiB");
        byte[] archive=zip(files);String id=sha(archive);
        if((long)archive.length+retainedBytes>MAX_RETAINED_BYTES)fail("DOCUMENT_CAPACITY","Retained native document byte budget reached");
        store.save(id,archive,hashes,partHashes,packageHashes);
        if(!retained.containsKey(id)){NativeDocumentStore.Record record=store.verify(id);retained.put(id,new Receipt(record.file,record.hashes,record.parts,record.packages));retainedBytes+=archive.length;}
        return new Saved(id,archive,manifest);
    }

    /** Verifies the retained archive and every entry/dependency before native Configuration.loadJob. */
    public synchronized Job reload(String id)throws Exception {
        return reloadWithMetadata(id).job;
    }

    /** Returns only optional association from verified owned bytes, never inferred current authority. */
    public synchronized Reloaded reloadWithMetadata(String id)throws Exception {
        if(id==null||!id.matches("[a-f0-9]{64}")||!retained.containsKey(id))fail("DOCUMENT_NOT_FOUND","Only retained self-generated native documents may be reloaded");
        if(reloads>=MAX_RELOADS)fail("DOCUMENT_CAPACITY","Native document reload limit reached");
        store.requireCapacity(MAX_BYTES);store.verify(id);
        Receipt receipt=retained.get(id);if(Files.isSymbolicLink(receipt.file))fail("PATH_REJECTED","Document artifact must be a regular retained file");
        byte[] archive=readBounded(receipt.file);if(!sha(archive).equals(id))fail("ARTIFACT_INTEGRITY","Retained native document SHA-256 does not match its receipt");
        LinkedHashMap<String,byte[]> files=unzipVerified(archive,receipt.hashes);
        LineageMetadata lineage=readLineage(files.get("manifest.json"));
        for(Map.Entry<String,String> dependency:receipt.parts.entrySet()){Part part=config.getPart(dependency.getKey());if(part==null||!fingerprint(part).equals(dependency.getValue()))fail("DOCUMENT_DEPENDENCY_CHANGED","Native part changed: "+dependency.getKey());}
        for(Map.Entry<String,String> dependency:receipt.packages.entrySet()){org.openpnp.model.Package pkg=config.getPackage(dependency.getKey());if(pkg==null||!fingerprint(pkg).equals(dependency.getValue()))fail("DOCUMENT_DEPENDENCY_CHANGED","Native package changed: "+dependency.getKey());}
        // Native resolver checks the current working directory before a job's directory. Refuse
        // collisions so none of our generated references can resolve to an unrelated local file.
        for(String name:files.keySet())if(!name.equals("manifest.json")&&Files.exists(Paths.get(name)))fail("PATH_COLLISION","Generated native asset name already exists in the process working directory");
        Path directory=Files.createTempDirectory(root,"load-");
        for(Map.Entry<String,byte[]> entry:files.entrySet())Files.write(directory.resolve(entry.getKey()),entry.getValue(),StandardOpenOption.CREATE_NEW);
        reloads++; // Native caches may retain definitions even if a following load fails.
        Job loaded=config.loadJob(directory.resolve("job.job.xml").toFile());
        new Graph(loaded); // Assert loaded native types/bounds too; never install a rejected result.
        return new Reloaded(loaded,lineage);
    }

    /** Stream only the optional metadata. Unknown ordinary manifest fields retain old behavior;
     * duplicate metadata/fields, invalid numeric types, and versions reject before native load. */
    private static LineageMetadata readLineage(byte[] bytes)throws Exception {
        try(JsonReader reader=new JsonReader(new InputStreamReader(new ByteArrayInputStream(bytes),java.nio.charset.StandardCharsets.UTF_8))){
            reader.setLenient(false);reader.beginObject();boolean found=false;LineageMetadata result=null;
            while(reader.hasNext()){
                String key=reader.nextName();
                if(!"lineage".equals(key)){reader.skipValue();continue;}
                if(found)fail("DOCUMENT_LINEAGE_INVALID","Duplicate lineage metadata");found=true;
                if(reader.peek()!=JsonToken.BEGIN_OBJECT)fail("DOCUMENT_LINEAGE_INVALID","Lineage metadata must be an object");
                reader.beginObject();Set<String> keys=new HashSet<>();String id=null;Long revision=null;Integer version=null;
                while(reader.hasNext()){
                    String field=reader.nextName();if(!keys.add(field))fail("DOCUMENT_LINEAGE_INVALID","Duplicate lineage field");
                    if("lineage_id".equals(field)){
                        if(reader.peek()!=JsonToken.STRING)fail("DOCUMENT_LINEAGE_INVALID","Lineage identifier must be a string");id=reader.nextString();
                    }else if("lineage_revision".equals(field)||"version".equals(field)){
                        if(reader.peek()!=JsonToken.NUMBER)fail("DOCUMENT_LINEAGE_INVALID","Lineage revision/version must be JSON numbers");
                        String number=reader.nextString();
                        if(number.length()>32)fail("DOCUMENT_LINEAGE_INVALID","Lineage numeric token exceeds its bound");
                        try{if("version".equals(field))version=new java.math.BigDecimal(number).intValueExact();else revision=new java.math.BigDecimal(number).longValueExact();}
                        catch(RuntimeException invalid){fail("DOCUMENT_LINEAGE_INVALID","Lineage revision/version must be exactly integral");}
                    }else fail("DOCUMENT_LINEAGE_INVALID","Unknown lineage field");
                }
                reader.endObject();if(!keys.equals(new HashSet<>(Arrays.asList("version","lineage_id","lineage_revision")))||version==null||version!=1||revision==null)
                    fail("DOCUMENT_LINEAGE_INVALID","Unsupported or incomplete lineage metadata version");
                result=new LineageMetadata(id,revision);
            }
            reader.endObject();if(reader.peek()!=JsonToken.END_DOCUMENT)fail("DOCUMENT_LINEAGE_INVALID","Trailing manifest data");return result;
        }catch(Bridge.Fault error){throw error;}catch(Exception error){throw new Bridge.Fault("DOCUMENT_LINEAGE_INVALID","Invalid verified manifest lineage metadata");}
    }

    private static final class Graph {
        final List<PlacementsHolderLocation<?>> locations=new ArrayList<>();
        final List<PlacementsHolder<?>> definitions=new ArrayList<>(),allHolders=new ArrayList<>();
        final LinkedHashMap<PlacementsHolder<?>,String> names=new LinkedHashMap<>();
        final Set<Part> parts=new LinkedHashSet<>();
        final Set<Object> seenLocations=Collections.newSetFromMap(new IdentityHashMap<>()),seenHolders=Collections.newSetFromMap(new IdentityHashMap<>());
        int placementCount,expandedPlacements,expandedLocations;
        Graph(Job job)throws Exception {
            if(job==null||job.getClass()!=Job.class)fail("UNSUPPORTED_NATIVE_DOCUMENT","Expected an exact native Job");
            PanelLocation root=job.getRootPanelLocation();if(root.getPanel()==null||root.getPanel().getClass()!=Panel.class)fail("UNSUPPORTED_NATIVE_DOCUMENT","Expected a native root panel");
            // The pinned resolver reconstructs linked-panel pseudo placements in loadPanel(),
            // but the job's inline root bypasses that path. Reject before any file rebinding.
            if(!root.getPanel().getPseudoPlacements().isEmpty()||(root.getPanel().getPseudoPlacementIds()!=null&&!root.getPanel().getPseudoPlacementIds().isEmpty()))fail("UNSUPPORTED_NATIVE_DOCUMENT","Root-panel pseudo placements are not reconstructed by this pinned native job loader");
            if(root.getPanel().getProfile()==root.getPanel().getProfile())fail("UNSUPPORTED_NATIVE_DOCUMENT","Explicit native root-panel outlines are not supported by this pinned native document round-trip");
            allHolders.add(root.getPanel());seenHolders.add(root.getPanel());checkPlacements(root.getPanel());
            expandedPlacements=root.getPanel().getPlacements().size()+root.getPanel().getPseudoPlacements().size();
            if(expandedPlacements>MAX_EXPANDED_PLACEMENTS)fail("DOCUMENT_GRAPH_LIMIT","Expanded native placement/fiducial records exceed 10000");
            for(PlacementsHolderLocation<?> child:root.getChildren()){countExpanded(child,Collections.newSetFromMap(new IdentityHashMap<>()),0);visit(child,Collections.newSetFromMap(new IdentityHashMap<>()),0);}
            if(locations.isEmpty())fail("EMPTY_JOB","Native job has no board or panel instances");
        }
        void countExpanded(PlacementsHolderLocation<?> location,Set<Object> ancestry,int depth)throws Exception {
            if(depth>8||++expandedLocations>MAX_LOCATIONS||location.getPlacementsHolder()==null)fail("DOCUMENT_GRAPH_LIMIT","Unresolved, deeply nested or oversized native graph");
            PlacementsHolder<?> holder=location.getPlacementsHolder();if(ancestry.contains(holder.getDefinition()))fail("PANEL_CYCLE","Native panel graph contains a cycle");
            expandedPlacements+=holder.getPlacements().size();if(holder.getClass()==Panel.class)expandedPlacements+=((Panel)holder).getPseudoPlacements().size();
            if(expandedPlacements>MAX_EXPANDED_PLACEMENTS)fail("DOCUMENT_GRAPH_LIMIT","Expanded native placement/fiducial records exceed 10000");
            if(holder.getClass()==Panel.class){Set<Object> next=Collections.newSetFromMap(new IdentityHashMap<>());next.addAll(ancestry);next.add(holder.getDefinition());for(PlacementsHolderLocation<?> child:((Panel)holder).getChildren())countExpanded(child,next,depth+1);}
        }
        void visit(PlacementsHolderLocation<?> location,Set<Object> ancestry,int depth)throws Exception {
            if(depth>8)fail("DOCUMENT_GRAPH_LIMIT","Native panel nesting exceeds eight levels");
            if(location.getClass()!=BoardLocation.class&&location.getClass()!=PanelLocation.class)fail("UNSUPPORTED_NATIVE_DOCUMENT","Unsupported native location subclass");
            PlacementsHolder<?> holder=location.getPlacementsHolder();
            if(holder==null||(holder.getClass()!=Board.class&&holder.getClass()!=Panel.class))fail("UNSUPPORTED_NATIVE_DOCUMENT","Unresolved or custom native definition");
            PlacementsHolder<?> definition=holder.getDefinition();
            if(definition==null||definition.getClass()!=holder.getClass())fail("UNSUPPORTED_NATIVE_DOCUMENT","Invalid native definition identity");
            if(ancestry.contains(definition))fail("PANEL_CYCLE","Native panel definition graph contains a cycle");
            finitePose(location.getLocation());
            if(!seenLocations.add(location))return;
            locations.add(location);if(locations.size()>MAX_LOCATIONS)fail("DOCUMENT_GRAPH_LIMIT","Native location count exceeds limit");
            validateInstance(holder,definition);
            if(seenHolders.add(holder)){allHolders.add(holder);checkPlacements(holder);}
            if(!names.containsKey(definition)) {
                if(definitions.size()>=MAX_ASSETS)fail("DOCUMENT_GRAPH_LIMIT","Native definition count exceeds 1000");
                // In this pinned native class, an absent profile getter allocates a new rectangle;
                // an explicit profile returns its stored object. Its native deserialize/copy path
                // is unqualified, so reject without private reflection or silently dropping it.
                if(definition.getProfile()==definition.getProfile())fail("UNSUPPORTED_NATIVE_DOCUMENT","Explicit native definition outlines are not supported by this pinned native document round-trip");
                definitions.add(definition);names.put(definition,String.format(java.util.Locale.ROOT,"asset-%04d.%s.xml",definitions.size(),definition.getClass()==Board.class?"board":"panel"));
                if(seenHolders.add(definition)){allHolders.add(definition);checkPlacements(definition);}
            }
            if(holder.getClass()==Panel.class){Set<Object> next=Collections.newSetFromMap(new IdentityHashMap<>());next.addAll(ancestry);next.add(definition);for(PlacementsHolderLocation<?> child:((Panel)holder).getChildren())visit(child,next,depth+1);if(holder!=definition)for(PlacementsHolderLocation<?> child:((Panel)definition).getChildren())visit(child,next,depth+1);}
        }
        void checkPlacements(PlacementsHolder<?> holder)throws Exception {
            finitePose(holder.getDimensions());validateProfile(holder.getProfile());Set<String> refs=new HashSet<>();
            if(holder.getClass()==Panel.class){Set<String> ids=new HashSet<>();for(Placement pseudo:((Panel)holder).getPseudoPlacements()){if(pseudo.getId()==null||!ids.add(pseudo.getId())||ids.size()>MAX_PLACEMENTS)fail("UNSUPPORTED_NATIVE_DOCUMENT","Pseudo-placement identifiers are missing, duplicated or oversized");finitePose(pseudo.getLocation());}}
            for(Placement placement:holder.getPlacements()) {
                if(++placementCount>MAX_PLACEMENTS)fail("DOCUMENT_GRAPH_LIMIT","Native definition/instance record traversal exceeds 100000");
                if(placement.getClass()!=Placement.class)fail("UNSUPPORTED_NATIVE_DOCUMENT","Custom placement subclasses are unsupported");
                if(placement.getId()==null||!refs.add(placement.getId()))fail("DUPLICATE_REFERENCE","Native definition has missing or duplicate references");
                finitePose(placement.getLocation());if(placement.getPart()!=null)parts.add(placement.getPart());
            }
        }
        void validateInstance(PlacementsHolder<?> holder,PlacementsHolder<?> definition)throws Exception {
            if(holder==definition)return;
            Area outlineDifference=new Area(holder.getProfile().convertToUnits(LengthUnit.Millimeters));outlineDifference.exclusiveOr(new Area(definition.getProfile().convertToUnits(LengthUnit.Millimeters)));if(!outlineDifference.isEmpty())fail("UNSUPPORTED_INSTANCE_EDIT","Native instance outline differs from its shared definition");
            if(!Objects.equals(holder.getName(),definition.getName())||!holder.getDimensions().equals(definition.getDimensions())||holder.getPlacements().size()!=definition.getPlacements().size())fail("UNSUPPORTED_INSTANCE_EDIT","Native instance differs from its shared definition beyond supported job overrides");
            for(Placement p:holder.getPlacements()){Placement d=definition.getPlacements().get(p.getId());if(d==null||!p.getLocation().equals(d.getLocation())||p.getSide()!=d.getSide()||p.getPart()!=d.getPart()||p.getType()!=d.getType()||!Objects.equals(p.getComments(),d.getComments())||p.getRank()!=d.getRank())fail("UNSUPPORTED_INSTANCE_EDIT","Placement instance geometry/content differs from shared definition: "+p.getId());}
            if(holder.getClass()==Panel.class){
                List<Placement> instances=((Panel)holder).getPseudoPlacements(),definitions=((Panel)definition).getPseudoPlacements();
                Map<String,Placement> expected=new HashMap<>();for(Placement placement:definitions)expected.put(placement.getId(),placement);
                if(instances.size()!=definitions.size()||expected.size()!=definitions.size())fail("UNSUPPORTED_INSTANCE_EDIT","Panel pseudo-placement identities differ from their shared definition");
                for(Placement p:instances){Placement d=expected.remove(p.getId());if(d==null||!p.getLocation().equals(d.getLocation())||p.getSide()!=d.getSide()||p.getPart()!=d.getPart()||p.getType()!=d.getType()||p.isEnabled()!=d.isEnabled()||p.getErrorHandling()!=d.getErrorHandling()||!Objects.equals(p.getComments(),d.getComments())||p.getRank()!=d.getRank())fail("UNSUPPORTED_INSTANCE_EDIT","Panel pseudo-placement instance differs from its shared definition: "+p.getId());}
            }
            if(holder.getClass()==Panel.class){List<PlacementsHolderLocation<?>> a=((Panel)holder).getChildren(),b=((Panel)definition).getChildren();if(a.size()!=b.size())fail("UNSUPPORTED_INSTANCE_EDIT","Panel instance child count differs from definition");for(int i=0;i<a.size();i++){PlacementsHolderLocation<?> x=a.get(i),y=b.get(i);if(!Objects.equals(x.getId(),y.getId())||x.getClass()!=y.getClass()||!x.getLocation().equals(y.getLocation())||x.getSide()!=y.getSide()||x.getPlacementsHolder().getDefinition()!=y.getPlacementsHolder().getDefinition())fail("UNSUPPORTED_INSTANCE_EDIT","Panel instance child pose differs from shared definition");}}
        }
    }

    private static LinkedHashMap<String,byte[]> unzipVerified(byte[] archive,Map<String,String> expected)throws Exception {
        LinkedHashMap<String,byte[]> files=new LinkedHashMap<>();int total=0;
        try(ZipInputStream zip=new ZipInputStream(new ByteArrayInputStream(archive))){ZipEntry entry;byte[] buffer=new byte[8192];while((entry=zip.getNextEntry())!=null){String name=entry.getName();if(entry.isDirectory()||!name.matches("(?:job\\.job\\.xml|manifest\\.json|asset-[0-9]{4}\\.(?:board|panel)\\.xml)")||!expected.containsKey(name)||files.containsKey(name)||files.size()>=MAX_ASSETS+2)fail("DOCUMENT_MANIFEST","Unexpected or duplicate native document entry");ByteArrayOutputStream out=new ByteArrayOutputStream();int n;while((n=zip.read(buffer))!=-1){total+=n;if(total>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Native document expands beyond 8 MiB");out.write(buffer,0,n);}byte[] bytes=out.toByteArray();if(!sha(bytes).equals(expected.get(name)))fail("ARTIFACT_INTEGRITY","Native document entry digest mismatch");files.put(name,bytes);}}
        if(!files.keySet().equals(expected.keySet()))fail("DOCUMENT_MANIFEST","Native document entries do not match retained manifest");return files;
    }
    private static byte[] zip(Map<String,byte[]> files)throws Exception {ByteArrayOutputStream out=new ByteArrayOutputStream();try(ZipOutputStream zip=new ZipOutputStream(out)){for(Map.Entry<String,byte[]> entry:files.entrySet()){ZipEntry ze=new ZipEntry(entry.getKey());ze.setTime(0);zip.putNextEntry(ze);zip.write(entry.getValue());zip.closeEntry();}}byte[] bytes=out.toByteArray();if(bytes.length>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Compressed native document exceeds 8 MiB");return bytes;}
    private static byte[] readBounded(Path path)throws Exception {if(!Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)||Files.size(path)>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Expected a regular native artifact up to 8 MiB");try(InputStream in=Files.newInputStream(path)){byte[] bytes=in.readNBytes(MAX_BYTES+1);if(bytes.length>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Native artifact exceeds byte limit");return bytes;}}
    private static String fingerprint(Object value)throws Exception {ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(value,out);return sha(out.toByteArray());}
    private static PlacementsHolder<?> copyDefinition(PlacementsHolder<?> value)throws Exception {ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(value,out);if(out.size()>MAX_BYTES)fail("DOCUMENT_TOO_LARGE","Native definition exceeds 8 MiB");return (PlacementsHolder<?>)Configuration.createSerializer().read(value.getClass(),new ByteArrayInputStream(out.toByteArray()));}
    private static String sha(byte[] bytes)throws Exception {StringBuilder hash=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))hash.append(String.format(java.util.Locale.ROOT,"%02x",b));return hash.toString();}
    private static void finitePose(Location pose)throws Exception {if(pose==null||!Double.isFinite(pose.getX())||!Double.isFinite(pose.getY())||!Double.isFinite(pose.getZ())||!Double.isFinite(pose.getRotation()))fail("INVALID_GEOMETRY","Native document geometry must be finite");}
    private static void validateProfile(GeometricPath2D profile)throws Exception{PathIterator iterator=profile.getPathIterator(null);double[] coordinates=new double[6];int segments=0;while(!iterator.isDone()){if(++segments>10000)fail("DOCUMENT_GRAPH_LIMIT","Native outline has too many path segments");int type=iterator.currentSegment(coordinates);int count=type==PathIterator.SEG_CLOSE?0:type==PathIterator.SEG_CUBICTO?6:type==PathIterator.SEG_QUADTO?4:2;for(int i=0;i<count;i++)if(!Double.isFinite(coordinates[i]))fail("INVALID_GEOMETRY","Native outline contains nonfinite coordinates");iterator.next();}}
    private static void deleteTree(Path directory)throws Exception {try(java.util.stream.Stream<Path> walk=Files.walk(directory)){List<Path> paths=new ArrayList<>();walk.forEach(paths::add);paths.sort(Comparator.reverseOrder());for(Path path:paths)Files.deleteIfExists(path);}}
    private static Map<String,Object> values(Object... pairs){Map<String,Object> result=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
    private static void fail(String code,String message)throws Bridge.Fault{throw new Bridge.Fault(code,message);}
}
