/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;

/** Actual native models, library identity/serialization, and passive job XML; no machine actions. */
public final class NativePartBindingsTest {
    static final Gson GSON = new Gson();
    static final List<String> passed = new ArrayList<>();
    static int assertions, libraryEvents;
    static Configuration config;
    static Part target;
    static Job published;
    static Path root;
    interface Checked { void run() throws Exception; }
    static void check(boolean ok, String text) { assertions++; if (!ok) throw new AssertionError(text); }
    static JsonObject obj(Object... values) { return GSON.toJsonTree(Bridge.map(values)).getAsJsonObject(); }
    static JsonArray rows(JsonObject... rows) { JsonArray array=new JsonArray();for(JsonObject row:rows)array.add(row);return array; }
    static JsonArray binding(String source, String nativeId) { return rows(obj("source_part_id",source,"native_part_id",nativeId)); }
    static JsonObject canonical() {
        JsonObject input=NativeImportBoundaryTest.canonical("SUPPLIER-1K",2);
        input.getAsJsonArray("parts").get(0).getAsJsonObject().addProperty("value","Supplier name must not overwrite native name");
        input.getAsJsonArray("instances").add(obj("id","second","kind","board","definitionId","b","x",10,"y",20,"z",0,"rotation",0,"side","top","enabled",true));
        return input;
    }
    static JsonObject part(JsonObject input,int index) { return input.getAsJsonArray("parts").get(index).getAsJsonObject(); }
    static JsonObject placement(JsonObject input,int index) { return input.getAsJsonArray("boards").get(0).getAsJsonObject().getAsJsonArray("placements").get(index).getAsJsonObject(); }
    static int listeners() throws Exception {
        java.lang.reflect.Field field=Configuration.class.getDeclaredField("listeners");field.setAccessible(true);
        return ((Set<?>)field.get(config)).size();
    }
    static String xml(Object value) throws Exception {
        StringWriter out=new StringWriter();Configuration.createSerializer().write(value,out);return out.toString();
    }
    static final class Snapshot {
        final List<Part> parts=config.getParts();
        final List<org.openpnp.model.Package> packages=config.getPackages();
        final IdentityHashMap<Object,String> serialized=new IdentityHashMap<>();
        final int listeners=listeners(),events=libraryEvents;
        final Job current=published;
        Snapshot() throws Exception { for(Object value:parts)serialized.put(value,xml(value));for(Object value:packages)serialized.put(value,xml(value)); }
        void unchanged() throws Exception {
            check(parts.equals(config.getParts())&&packages.equals(config.getPackages()),"Library object lists unchanged");
            check(listeners==listeners(),"No Part constructor/configuration listener allocation");
            check(events==libraryEvents,"No parts/packages publication events");
            check(current==published,"Existing published job identity unchanged");
            for(Map.Entry<Object,String> entry:serialized.entrySet()) check(entry.getValue().equals(xml(entry.getKey())),"Complete native Part/Package XML unchanged");
        }
    }
    static void refused(String code, Checked work) throws Exception {
        Snapshot before=new Snapshot();
        try { work.run();throw new AssertionError("Expected "+code); }
        catch(Bridge.Fault fault) { check(code.equals(fault.code),"Expected "+code+", got "+fault.code); }
        before.unchanged();passed.add("refused:"+code);
    }
    static void validateJob(Job job, Part expected, int boards, int placements) {
        check(job.getBoardLocations().size()==boards,"Expected native BoardLocation count");
        for(BoardLocation location:job.getBoardLocations()) {
            check(location.getBoard().getPlacements().size()==placements,"Expected native placement count");
            for(Placement placement:location.getBoard().getPlacements()) {
                check(placement.getPart()==expected,"Native placement references exact configured target");
                check(!job.retrievePlacedStatus(location,placement.getId()),"Import and saved Job reload do not confer placement history");
            }
        }
        if(boards>1) {
            Board a=job.getBoardLocations().get(0).getBoard(),b=job.getBoardLocations().get(1).getBoard();
            check(a!=b&&a.getDefinition()==b.getDefinition(),"Distinct native instances retain shared board definition");
            check(a.getPlacements().get(0)!=b.getPlacements().get(0),"Native placement instances remain separate");
        }
    }
    static final class CustomPart extends Part { CustomPart(String id){super(id);} }
    static final class CustomPackage extends org.openpnp.model.Package { CustomPackage(String id){super(id);} }

    public static void main(String[] args) throws Exception {
        root=Files.createTempDirectory("openpnp-part-bindings-");
        Configuration.initialize(root.toFile());config=Configuration.get();config.load();int exit=0;
        try {
            target=config.getPart("R0603-1K");check(target!=null,"Pinned configured target exists");
            config.addPropertyChangeListener("parts",e->libraryEvents++);
            config.addPropertyChangeListener("packages",e->libraryEvents++);
            published=CanonicalJobImporter.load(config,NativeImportBoundaryTest.canonical(target.getId(),1));
            config.save();byte[] partsBefore=Files.readAllBytes(root.resolve("parts.xml"));
            byte[] packagesBefore=Files.readAllBytes(root.resolve("packages.xml"));
            Snapshot positiveBefore=new Snapshot();
            JsonObject input=canonical();JsonArray explicit=binding("supplier-1k","r0603-1k");
            NativePartBindings resolver=NativePartBindings.resolve(config,input,explicit);
            String digest=resolver.canonicalSha256();
            List<Map<String,Object>> evidence=resolver.provenance();
            check(evidence.size()==1&&evidence.get(0).get("source_part_id").equals("SUPPLIER-1K"),"Provenance preserves canonical source spelling");
            check(evidence.get(0).get("native_part_id").equals(target.getId())&&evidence.get(0).get("definition_placement_count").equals(2),"Provenance records native identity and definition count, not expanded count");
            explicit.get(0).getAsJsonObject().addProperty("native_part_id","DOES-NOT-EXIST");
            Job job=CanonicalJobImporter.loadExistingParts(config,input,resolver);
            validateJob(job,target,2,2);
            check(digest.equals(resolver.canonicalSha256()),"Mutating supplied binding rows cannot mutate frozen resolution");
            try { evidence.clear();throw new AssertionError("Expected immutable list"); } catch(UnsupportedOperationException expected){assertions++;}
            try { evidence.get(0).put("native_part_id","Other");throw new AssertionError("Expected immutable row"); } catch(UnsupportedOperationException expected){assertions++;}
            NativeJobDocuments documents=new NativeJobDocuments(config,root.resolve("documents"));
            NativeJobDocuments.Saved saved=documents.save(job);Job reloaded=documents.reload(saved.sha256);
            validateJob(reloaded,target,2,2);
            check(config.getPart("SUPPLIER-1K")==null,"Source alias is never installed in native library");
            positiveBefore.unchanged();
            config.save();
            check(Arrays.equals(partsBefore,Files.readAllBytes(root.resolve("parts.xml"))),"Saved parts.xml byte-identical after alias import/job reload");
            check(Arrays.equals(packagesBefore,Files.readAllBytes(root.resolve("packages.xml"))),"Saved packages.xml byte-identical after alias import/job reload");
            passed.add("case-insensitive explicit resolution, immutable provenance, repeated native instances and real Job XML reload preserve complete library");

            JsonObject many=canonical();many.getAsJsonArray("parts").add(obj("id","SECOND-SUPPLIER","packageId","R0603","heightMm",0.75));
            placement(many,1).addProperty("partId","SECOND-SUPPLIER");
            JsonArray multi=rows(obj("source_part_id","SUPPLIER-1K","native_part_id",target.getId()),obj("source_part_id","SECOND-SUPPLIER","native_part_id",target.getId()));
            Snapshot manyBefore=new Snapshot();validateJob(CanonicalJobImporter.load(config,many,multi),target,2,2);manyBefore.unchanged();
            JsonObject implicit=canonical();implicit.getAsJsonArray("parts").add(obj("id","r0603-1k","packageId","r0603","heightMm",0.75));
            placement(implicit,1).addProperty("partId","R0603-1K");
            NativePartBindings implicitResolver=NativePartBindings.resolve(config,implicit,binding("SUPPLIER-1K",target.getId()));
            check(Boolean.FALSE.equals(implicitResolver.provenance().get(1).get("explicit_binding")),"Unmapped canonical identity resolves existing native part directly");
            validateJob(CanonicalJobImporter.loadExistingParts(config,implicit,implicitResolver),target,2,2);
            passed.add("many source IDs can explicitly resolve one compatible target; remaining source IDs must already exist");

            // A real existing source may explicitly redirect without modifying either Part.
            Part originalSource=config.getPart("R0805-1K");check(originalSource!=null,"Pinned alternate source exists");
            JsonObject redirect=NativeImportBoundaryTest.canonical(originalSource.getId(),1);
            Snapshot redirectBefore=new Snapshot();validateJob(CanonicalJobImporter.load(config,redirect,binding(originalSource.getId(),target.getId())),target,1,1);redirectBefore.unchanged();

            JsonObject maximum=canonical();JsonArray maxBindings=binding("SUPPLIER-1K",target.getId());
            for(int i=1;i<1000;i++) { String source="UNUSED-SUPPLIER-"+i;maximum.getAsJsonArray("parts").add(obj("id",source,"packageId","R0603","heightMm",0.75));maxBindings.add(obj("source_part_id",source,"native_part_id",target.getId())); }
            Snapshot maximumBefore=new Snapshot();NativePartBindings maxResolver=NativePartBindings.resolve(config,maximum,maxBindings);
            validateJob(CanonicalJobImporter.loadExistingParts(config,maximum,maxResolver),target,2,2);
            check(maxResolver.provenance().size()==1000&&maxResolver.provenance().get(999).get("definition_placement_count").equals(0),"1000 explicit source bindings accepted, unused declarations identified");maximumBefore.unchanged();
            Length mmHeight=target.getHeight();target.setHeight(new Length(0.75/25.4,LengthUnit.Inches));
            Snapshot inchesBefore=new Snapshot();validateJob(CanonicalJobImporter.load(config,canonical(),binding("SUPPLIER-1K",target.getId())),target,2,2);inchesBefore.unchanged();target.setHeight(mmHeight);
            passed.add("1000-binding boundary and native inch-height target resolve without library edits");

            refused("INVALID_PART_BINDINGS",()->CanonicalJobImporter.load(config,canonical(),(JsonArray)null));
            refused("INVALID_PART_BINDINGS",()->CanonicalJobImporter.load(config,canonical(),new JsonArray()));
            refused("INVALID_PART_BINDINGS",()->CanonicalJobImporter.loadExistingParts(config,canonical(),null));
            refused("INVALID_PART_BINDINGS",()->CanonicalJobImporter.load(config,canonical(),rows(obj("source_part_id","SUPPLIER-1K","native_part_id",target.getId(),"create",true))));
            refused("INVALID_PART_BINDINGS",()->CanonicalJobImporter.load(config,canonical(),rows(obj("source_part_id","SUPPLIER-1K"))));
            refused("INVALID_ARGUMENT",()->{JsonArray bad=new JsonArray();bad.add(new JsonPrimitive("not-object"));CanonicalJobImporter.load(config,canonical(),bad);});
            refused("INVALID_ID",()->CanonicalJobImporter.load(config,canonical(),binding("SUPPLIER 1K",target.getId())));
            refused("INVALID_ID",()->CanonicalJobImporter.load(config,canonical(),binding("SUPPLIER-1K","bad/target")));
            refused("INVALID_ID",()->CanonicalJobImporter.load(config,canonical(),rows(obj("source_part_id",123,"native_part_id",target.getId()))));
            refused("DUPLICATE_PART_BINDING",()->CanonicalJobImporter.load(config,canonical(),rows(obj("source_part_id","SUPPLIER-1K","native_part_id",target.getId()),obj("source_part_id","supplier-1k","native_part_id",target.getId()))));
            refused("UNKNOWN_PART_BINDING",()->{JsonObject own=NativeImportBoundaryTest.canonical(target.getId(),1);CanonicalJobImporter.load(config,own,binding("Absent",target.getId()));});
            refused("PART_UNMAPPED",()->CanonicalJobImporter.load(config,canonical(),binding("SUPPLIER-1K","Absent")));
            refused("PART_UNMAPPED",()->CanonicalJobImporter.load(config,many,rows(obj("source_part_id","SUPPLIER-1K","native_part_id","SECOND-SUPPLIER"),obj("source_part_id","SECOND-SUPPLIER","native_part_id",target.getId()))));
            refused("PART_UNMAPPED",()->{JsonObject invalid=canonical();invalid.getAsJsonArray("parts").add(obj("id","Unmapped","packageId","R0603","heightMm",0.75));CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("PACKAGE_UNMAPPED",()->{JsonObject invalid=canonical();part(invalid,0).addProperty("packageId","Absent");CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("PART_CONFLICT",()->{JsonObject invalid=canonical();part(invalid,0).addProperty("packageId","R0805");CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("PART_CONFLICT",()->{JsonObject invalid=canonical();part(invalid,0).addProperty("heightMm",0.8);CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("PART_CONFLICT",()->{JsonObject invalid=canonical();placement(invalid,1).addProperty("heightMm",0.8);CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("DUPLICATE_ID",()->{JsonObject invalid=canonical();invalid.getAsJsonArray("parts").add(obj("id","supplier-1k","packageId","R0603","heightMm",0.75));CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("MISSING_DEFINITION",()->{JsonObject invalid=canonical();invalid.getAsJsonArray("instances").get(1).getAsJsonObject().addProperty("definitionId","missing-late");CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("DUPLICATE_REFERENCE",()->{JsonObject invalid=canonical();placement(invalid,1).addProperty("ref","R0");CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("OUTSIDE_BOARD",()->{JsonObject invalid=canonical();placement(invalid,1).addProperty("x",151);CanonicalJobImporter.load(config,invalid,binding("SUPPLIER-1K",target.getId()));});
            refused("INVALID_PART_BINDINGS",()->{JsonArray tooMany=new JsonArray();for(int i=0;i<1001;i++)tooMany.add(obj("source_part_id","A"+i,"native_part_id",target.getId()));CanonicalJobImporter.load(config,canonical(),tooMany);});
            refused("INPUT_TOO_LARGE",()->{JsonObject large=canonical();large.addProperty("unused",String.join("",Collections.nCopies(9*1024*1024,"x")));CanonicalJobImporter.load(config,large,binding("SUPPLIER-1K",target.getId()));});
            refused("INPUT_TOO_LARGE",()->{JsonObject deep=canonical(),node=deep;for(int i=0;i<66;i++){JsonObject next=new JsonObject();node.add("unused",next);node=next;}CanonicalJobImporter.load(config,deep,binding("SUPPLIER-1K",target.getId()));});

            refused("PART_BINDINGS_STALE",()->{JsonObject changed=new JsonParser().parse(input.toString()).getAsJsonObject();placement(changed,1).addProperty("x",5);CanonicalJobImporter.loadExistingParts(config,changed,resolver);});
            refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(null,input,resolver));
            Length originalHeight=target.getHeight();target.setHeight(new Length(0.8,LengthUnit.Millimeters));
            refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(config,input,resolver));target.setHeight(originalHeight);
            target.setHeight(new Length(Double.NaN,LengthUnit.Millimeters));
            refused("PART_CONFLICT",()->CanonicalJobImporter.load(config,canonical(),binding("SUPPLIER-1K",target.getId())));target.setHeight(originalHeight);
            org.openpnp.model.Package originalPackage=target.getPackage();target.setPackage(config.getPackage("R0805"));
            refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(config,input,resolver));target.setPackage(originalPackage);
            String originalId=target.getId();target.setId("Renamed-Part");
            refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(config,input,resolver));target.setId(originalId);
            String packageId=originalPackage.getId();originalPackage.setId("Renamed-Package");
            refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(config,input,resolver));originalPackage.setId(packageId);
            Part replacement=new Part(target.getId());replacement.setPackage(originalPackage);replacement.setHeight(originalHeight);config.addPart(replacement);
            refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(config,input,resolver));config.addPart(target);
            org.openpnp.model.Package replacementPackage=new org.openpnp.model.Package(packageId);config.addPackage(replacementPackage);
            refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(config,input,resolver));config.addPackage(originalPackage);
            CustomPart custom=new CustomPart("Custom-Target");custom.setPackage(originalPackage);custom.setHeight(originalHeight);config.addPart(custom);
            refused("UNSUPPORTED_NATIVE_PROFILE",()->CanonicalJobImporter.load(config,canonical(),binding("SUPPLIER-1K",custom.getId())));config.removePart(custom);
            CustomPackage customPackage=new CustomPackage(packageId);config.addPackage(customPackage);
            refused("UNSUPPORTED_NATIVE_PROFILE",()->CanonicalJobImporter.load(config,canonical(),binding("SUPPLIER-1K",target.getId())));config.addPackage(originalPackage);
            Locale oldLocale=Locale.getDefault();try {
                Locale.setDefault(Locale.US);JsonObject localeInput=NativeImportBoundaryTest.canonical("alias",1);
                NativePartBindings localeResolver=NativePartBindings.resolve(config,localeInput,binding("alias",target.getId()));
                Locale.setDefault(Locale.forLanguageTag("tr-TR"));
                refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(config,localeInput,localeResolver));
            } finally { Locale.setDefault(oldLocale); }
            validateJob(CanonicalJobImporter.loadExistingParts(config,input,resolver),target,2,2);
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Native machine remained disabled and unhomed");
            Configuration.initialize(root.resolve("other-configuration").toFile());Configuration other=Configuration.get();other.load();
            try { refused("PART_BINDINGS_STALE",()->CanonicalJobImporter.loadExistingParts(other,input,resolver)); }
            finally { other.getMachine().close(); }
            Map<String,Object> result=Bridge.map("assertions",assertions,"checks",passed,"job_bundle_sha256",saved.sha256,
                    "resolver_canonical_sha256",digest,"resolution",evidence,"library_xml_unchanged",true,
                    "upstream_commit",Bridge.UPSTREAM,"native_machine_enabled",false,"native_machine_homed",false,
                    "native_feed_effects",0,"placements_executed",0,"physical_qualification",false,
                    "importer_code_origin",CanonicalJobImporter.class.getProtectionDomain().getCodeSource().getLocation().toString(),
                    "native_code_origin",Part.class.getProtectionDomain().getCodeSource().getLocation().toString(),
                    "bridge_code_origin",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString());
            Files.writeString(root.resolve("result.json"),GSON.toJson(result));
            System.out.println("OPENPNP_NATIVE_PART_BINDINGS_RESULT "+GSON.toJson(result));
        } catch(Throwable failure){failure.printStackTrace();exit=1;}finally{config.getMachine().close();}
        System.exit(exit);
    }
}
