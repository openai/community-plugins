/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.charset.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.util.Utils2D;
import org.w3c.dom.*;
import static org.openpnp.codex.NativePortableConfiguration.*;
import static org.openpnp.codex.NativePortableBoardLibrary.children;
import static org.openpnp.codex.NativePortableBoardLibrary.attributes;
import static org.openpnp.codex.NativePortableBoardLibrary.noText;

/** Closed modern panel definitions, with direct references to the captured Board library only. */
public final class NativePortablePanelLibrary {
    static final String PROFILE="saved-board-child-panel-library-v1",SEP="⇒";
    static final int MAX_PANELS=32,MAX_HOLDERS=1000,MAX_RECORDS=10000;
    private NativePortablePanelLibrary(){}
    public static Map<String,Object> describe(){return Bridge.map("profile",PROFILE,"archive_version",3,"panels",MAX_PANELS,"boards",32,"file_bytes",MAX_FILE,"archive_bytes",MAX_BUNDLE,"archive_files",MAX_FILES,"manifest_bytes",256*1024,"panel_xml_elements_total",50000,"panel_xml_text_total",2*1024*1024,"local_id_utf16",128,"pseudo_id_utf16",512,"holders_total",MAX_HOLDERS,"expanded_records_total",MAX_RECORDS,"source","saved-clean-exact-native-Panel-2.0","children","direct-exact-BoardLocation-existing-library-definitions","nested_panels",false,"custom_outlines",false,"job_documents",false,"operational_history",false,"material_counters",false,"registration_authority",false,"physical_authority",false);}
    static final class Captured {
        final Map<String,byte[]> entries=new TreeMap<>();
        final List<Map<String,Object>> records=new ArrayList<>();
        final List<String> sourcePaths=new ArrayList<>();
        final List<Panel> identities;
        final List<Board> boards;
        final List<Part> partIdentities;final List<org.openpnp.model.Package> packageIdentities;
        final List<String> boardModelHashes=new ArrayList<>(),boardPaths=new ArrayList<>();
        final Map<Path,String> sourceHashes=new LinkedHashMap<>();final Map<Path,Object> sourceKeys=new LinkedHashMap<>();
        final IdentityHashMap<Object,String> library=new IdentityHashMap<>();
        final List<String> modelHashes=new ArrayList<>();
        final IdentityHashMap<Board,String> boardIds=new IdentityHashMap<>();
        Captured(Configuration c){identities=new ArrayList<>(c.getPanels());boards=new ArrayList<>(c.getBoards());partIdentities=new ArrayList<>(c.getParts());packageIdentities=new ArrayList<>(c.getPackages());}
        Map<String,Object> manifest(){int children=0,own=0,pseudo=0,expanded=0;for(Map<String,Object> row:records){children+=(int)row.get("child_count");own+=(int)row.get("placement_count");pseudo+=(int)row.get("pseudo_count");expanded+=(int)row.get("expanded_record_count");}return Bridge.map("profile",PROFILE,"panels",records,"panel_count",records.size(),"child_count",children,"placement_count",own,"pseudo_count",pseudo,"expanded_record_count",expanded);}
        void verifyCurrent(Configuration c)throws Exception {
            if(!sameIdentity(identities,c.getPanels())||!sameIdentity(boards,c.getBoards())||!sameIdentity(partIdentities,c.getParts())||!sameIdentity(packageIdentities,c.getPackages()))reject("Native library registry identity changed during capture");
            for(Map.Entry<Path,String> e:sourceHashes.entrySet())if(!Objects.equals(sourceKeys.get(e.getKey()),Files.readAttributes(e.getKey(),java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey())||!sha(read(e.getKey(),MAX_FILE)).equals(e.getValue()))reject("Source definition changed during capture");
            for(Map.Entry<Object,String> e:library.entrySet()){
                Object o=e.getKey();if(o instanceof Part&&c.getPart(((Part)o).getId())!=o||o instanceof org.openpnp.model.Package&&c.getPackage(((org.openpnp.model.Package)o).getId())!=o)reject("Part/package canonical identity changed");
                if(!sha(serialize(o)).equals(e.getValue()))reject("Part/package definition changed during capture");
            }
            for(int i=0;i<boards.size();i++){Board board=boards.get(i);NativePortableBoardLibrary.validateModel(c,board);if(board.getDefinition()!=board||board.isDirty()||!sourceFile(board.getFile()).toString().equals(boardPaths.get(i))||!semantic(xml(NativePortableBoardLibrary.serialize(board))).equals(boardModelHashes.get(i)))reject("Captured Board model/file/dirty state changed");}
            for(int i=0;i<identities.size();i++){Panel panel=identities.get(i);validateModel(c,panel,boardIds);if(panel.isDirty()||!panel.getFile().toPath().toAbsolutePath().normalize().toString().equals(sourcePaths.get(i)))reject("Panel file or dirty state changed");Document doc=xml(serialize(panel));normalizeSource(doc,panel.getFile().toPath(),boardIds,c);if(!semantic(doc).equals(modelHashes.get(i)))reject("Native panel model changed during capture");}
        }
    }
    static Captured capture(Configuration c,NativePortableBoardLibrary.Captured capturedBoards)throws Exception {
        Captured out=new Captured(c);if(out.identities.isEmpty()||out.identities.size()>MAX_PANELS)reject("Version3 requires1..32 panel definitions");
        Map<String,Document> boardDocs=new LinkedHashMap<>();Set<Path> files=new HashSet<>();Set<Object> fileKeys=new HashSet<>();
        for(int i=0;i<out.boards.size();i++){Board b=out.boards.get(i);String id=String.format(Locale.ROOT,"board-%03d",i+1);out.boardIds.put(b,id);Path path=sourceFile(b.getFile());uniqueFile(path,files,fileKeys);out.sourceHashes.put(path,sha(read(path,MAX_FILE)));out.sourceKeys.put(path,Files.readAttributes(path,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey());String entry=(String)capturedBoards.records.get(i).get("entry");boardDocs.put(id,xml(capturedBoards.entries.get(entry)));out.boardPaths.add(path.toString());out.boardModelHashes.add((String)capturedBoards.records.get(i).get("semantic_sha256"));}
        for(Part p:c.getParts())out.library.put(p,sha(serialize(p)));for(org.openpnp.model.Package p:c.getPackages())out.library.put(p,sha(serialize(p)));
        Set<String> parts=partIds(c);Set<Panel> distinct=Collections.newSetFromMap(new IdentityHashMap<>());int[] budget=new int[4];int holders=out.identities.size(),expanded=0;
        for(Panel panel:out.identities){
            if(panel.getClass()!=Panel.class||panel.getDefinition()!=panel||!distinct.add(panel))reject("Only distinct exact self-defining native Panels are portable");
            if(panel.isDirty()||panel.getFile()==null)fail("LIBRARY_DIRTY","Save each panel before portable export; no save dialog is invoked");
            Path path=sourceFile(panel.getFile());uniqueFile(path,files,fileKeys);byte[] saved=read(path,MAX_FILE);out.sourceHashes.put(path,sha(saved));out.sourceKeys.put(path,Files.readAttributes(path,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey());
            Document savedDoc=xml(saved);normalizeSource(savedDoc,path,out.boardIds,c);Shape savedShape=validatePanel(savedDoc,boardDocs,parts,new int[4]);if(holders+savedShape.childCount>MAX_HOLDERS||expanded+savedShape.expanded>MAX_RECORDS)reject("Cumulative panel expansion exceeds bounds before native copies");
            validateModel(c,panel,out.boardIds);Document current=xml(serialize(panel));normalizeSource(current,path,out.boardIds,c);Shape shape=validatePanel(current,boardDocs,parts,budget);
            // Only prevalidated, bounded XML enters a native reader; bind in-memory board copies explicitly.
            try(DetachedPanel normalized=readDetached(savedDoc,c,out.boardIds)){Document normalizedDoc=xml(serialize(normalized.panel));normalizeSource(normalizedDoc,path,out.boardIds,c);if(!semantic(current).equals(semantic(normalizedDoc)))fail("LIBRARY_CHANGED","Saved panel bytes differ from the current native definition");}
            holders+=shape.childCount;expanded+=shape.expanded;if(holders>MAX_HOLDERS||expanded>MAX_RECORDS)reject("Cumulative panel expansion exceeds bounds");
            byte[] bytes=encode(current);String id=String.format(Locale.ROOT,"panel-%03d",out.records.size()+1),digest=sha(bytes),entry="library/panels/"+id+"-"+digest+".panel.xml";
            out.entries.put(entry,bytes);out.sourcePaths.add(path.toString());out.modelHashes.add(semantic(current));out.records.add(Bridge.map("id",id,"entry",entry,"sha256",digest,"semantic_sha256",semantic(current),"source_sha256",sha(saved),"child_count",shape.childCount,"placement_count",shape.ownCount,"pseudo_count",shape.pseudoCount,"expanded_record_count",shape.expanded,"board_bindings",shape.bindings,"pseudo_bindings",shape.pseudos));
        }
        out.verifyCurrent(c);return out;
    }
    static Path sourceFile(File file)throws Exception {if(file==null)reject("Saved file required");Path p=file.toPath().toAbsolutePath().normalize();checkAncestors(p);if(!Files.isRegularFile(p,LinkOption.NOFOLLOW_LINKS)||Files.size(p)>MAX_FILE)reject("Expected bounded regular library source");return p.toRealPath();}
    static void uniqueFile(Path p,Set<Path> files,Set<Object> keys)throws Exception {Object key=Files.readAttributes(p,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey();if(!files.add(p)||key!=null&&!keys.add(key))reject("Library file identity is aliased");}
    static boolean sameIdentity(List<?> a,List<?> b){if(a.size()!=b.size())return false;for(int i=0;i<a.size();i++)if(a.get(i)!=b.get(i))return false;return true;}
    static Set<String> partIds(Configuration c){Set<String> ids=new HashSet<>();for(Part p:c.getParts())ids.add(p.getId());return ids;}
    static byte[] serialize(Object object)throws Exception {ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(object,out);byte[] bytes=out.toByteArray();if(bytes.length>MAX_FILE)reject("Serialized panel/model exceeds file bound");return bytes;}
    static void identifier(String id,int max)throws Exception {if(id==null||id.isEmpty()||id.length()>max||id.chars().anyMatch(Character::isISOControl))reject("Malformed or oversized native identifier");try{StandardCharsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(id));}catch(CharacterCodingException e){reject("Malformed Unicode identifier");}}
    static void localId(String id)throws Exception {identifier(id,128);if(id.contains(SEP))reject("Native identifier contains path delimiter");}
    static Element one(Element root,String tag)throws Exception {Element found=null;for(Element e:children(root))if(e.getTagName().equals(tag)){if(found!=null)reject("Duplicate native field "+tag);found=e;}return found;}
    static List<Element> childElements(Document doc)throws Exception {Element group=one(doc.getDocumentElement(),"children");return group==null?List.of():children(group);}
    static List<String> pseudoIds(Document doc)throws Exception {Element group=one(doc.getDocumentElement(),"pseudo-placement-ids");List<String> ids=new ArrayList<>();if(group!=null)for(Element e:children(group))ids.add(e.getTextContent());return ids;}
    static void normalizeSource(Document doc,Path panelFile,IdentityHashMap<Board,String> ids,Configuration c)throws Exception {
        for(Element child:childElements(doc)){
            String ref=child.getAttribute("file-name");if(ref.isEmpty()||ref.startsWith("codex-")||ref.indexOf('\0')>=0)reject("Missing or nonnative source board reference");
            Path supplied=Path.of(ref),actual;
            if(supplied.isAbsolute())actual=supplied;
            else {if(supplied.getNameCount()!=1||ref.equals(".")||ref.equals(".."))reject("Relative source board reference must be an existing colocated basename");actual=panelFile.toAbsolutePath().getParent().resolve(supplied);Path cwd=supplied.toAbsolutePath();if(Files.exists(cwd,LinkOption.NOFOLLOW_LINKS)&&!Files.isSameFile(cwd,actual))reject("Source board basename resolves differently in the current directory");}
            Path path=sourceFile(actual.toFile());Board target=null;for(Board board:c.getBoards())if(ids.containsKey(board)&&sourceFile(board.getFile()).equals(path)){if(target!=null)reject("Ambiguous board file reference");target=board;}
            if(target==null)reject("Panel child is outside the captured board library");child.setAttribute("file-name","codex-board:"+ids.get(target));
        }
    }
    /** Owns only the private deserialized model and copies made below, never library definitions. */
    static final class DetachedPanel implements AutoCloseable {
        final Panel panel;
        final List<Board> boards=new ArrayList<>();
        boolean closed;
        DetachedPanel(Panel panel){this.panel=panel;}
        @Override public void close(){
            if(closed)return;closed=true;
            Throwable failure=null;
            // PseudoPlacement.dispose needs intact branch/leaf bindings to detach its native listeners.
            for(Placement pseudo:new ArrayList<>(panel.getPseudoPlacements())){
                failure=cleanup(failure,()->pseudo.removePropertyChangeListener(panel));
                failure=cleanup(failure,pseudo::dispose);
            }
            panel.getPseudoPlacements().clear();
            for(PlacementsHolderLocation<?> child:new ArrayList<>(panel.getChildren())){
                // Exact deserialized BoardLocation owns only its self, Panel and pseudo listeners.
                // Its native dispose consults MainFrame even when unparented; do not call it here.
                failure=cleanup(failure,()->child.removePropertyChangeListener(panel));
                failure=cleanup(failure,()->child.removePropertyChangeListener(child));
            }
            panel.getChildren().clear();
            for(Board board:boards)failure=cleanup(failure,board::dispose);
            boards.clear();
            // With children/pseudos detached, native disposal releases own placements and Panel listeners.
            failure=cleanup(failure,panel::dispose);
            if(failure instanceof RuntimeException)throw (RuntimeException)failure;
            if(failure instanceof Error)throw (Error)failure;
        }
        private static Throwable cleanup(Throwable failure,Runnable action){
            try{action.run();}catch(RuntimeException|Error e){if(failure==null)return e;if(failure!=e)failure.addSuppressed(e);}
            return failure;
        }
    }
    static DetachedPanel readDetached(Document logical,Configuration c,IdentityHashMap<Board,String> ids)throws Exception {
        Panel p=Configuration.createSerializer().read(Panel.class,new ByteArrayInputStream(encode(logical)));
        DetachedPanel owned=new DetachedPanel(p);
        try {for(PlacementsHolderLocation<?> loc:p.getChildren()){String id=loc.getFileName();Board target=null;for(Map.Entry<Board,String> e:ids.entrySet())if(id.equals("codex-board:"+e.getValue()))target=e.getKey();if(target==null)reject("Unresolved detached board");Board copy=new Board(target);owned.boards.add(copy);loc.setPlacementsHolder(copy);}
            for(String id:new ArrayList<>(p.getPseudoPlacementIds()))p.addPseudoPlacement(p.createPseudoPlacement(id));p.setDirty(false);validateModel(c,p,ids);return owned;
        }catch(Exception|Error e){try{owned.close();}catch(RuntimeException|Error cleanup){if(e!=cleanup)e.addSuppressed(cleanup);}throw e;}
    }
    static void validateModel(Configuration c,Panel panel,IdentityHashMap<Board,String> ids)throws Exception {
        if(panel.getClass()!=Panel.class||panel.getDefinition()!=panel||panel.getVersion()==null||Double.compare(panel.getVersion(),2.0)!=0)reject("Exact modern native panel required");
        if(panel.id!=null||panel.xGap!=null||panel.yGap!=null||panel.columns!=null&&panel.columns!=1||panel.rows!=null&&panel.rows!=1)reject("Legacy panel layout state is unsupported");
        if(panel.getProfile()==panel.getProfile())reject("Explicit panel outlines are unsupported");
        Set<String> locals=new HashSet<>();for(Placement p:panel.getPlacements()){localId(p.getId());if(p.getClass()!=Placement.class||p.getType()!=Placement.Type.Fiducial||!locals.add(p.getId()))reject("Panel own placements must be unique exact Fiducials");canonicalPart(c,p);}
        locals.clear();for(PlacementsHolderLocation<?> item:panel.getChildren()){
            if(item.getClass()!=BoardLocation.class||item.getDefinition()!=item||item.getParent()!=null)reject("Panel definition needs direct unparented exact BoardLocation definitions");BoardLocation child=(BoardLocation)item;localId(child.getId());if(!locals.add(child.getId()))reject("Duplicate panel child ID");
            if(child.getPlaced()!=null&&!child.getPlaced().isEmpty())reject("Legacy placed history cannot transfer");if(child.getPlacementsTransformStatus()!=PlacementsHolderLocation.PlacementsTransformStatus.NotSet||!child.getLocalToParentTransform().equals(Utils2D.getDefaultBoardPlacementLocationTransform(child)))reject("Measured or non-nominal child registration cannot transfer");
            Board board=child.getBoard();if(board==null||board.getClass()!=Board.class||!ids.containsKey(board.getDefinition()))reject("Unknown canonical child board definition");if(!sourceFile(board.getDefinition().getFile()).toString().equals(child.getFileName()))reject("Native child filename and board identity differ");NativePortableBoardLibrary.validateModel(c,board);for(Placement p:board.getPlacements())localId(p.getId());
            if(!semantic(xml(NativePortableBoardLibrary.serialize(board))).equals(semantic(xml(NativePortableBoardLibrary.serialize(board.getDefinition())))))reject("Child board instance differs from its captured definition");
        }
        List<String> pseudoIds=new ArrayList<>();Set<String> unique=new HashSet<>();for(Placement actual:panel.getPseudoPlacements()){
            identifier(actual.getId(),512);if(actual.getClass()!=PseudoPlacement.class||!unique.add(actual.getId()))reject("Only exact native derived pseudo placements are portable");
            var pair=panel.getDescendantPlacement(actual.getId());if(pair==null||pair.first.size()!=1||pair.second==null||!(pair.first.get(0).getId()+SEP+pair.second.getId()).equals(actual.getId())||!panel.getChildren().stream().anyMatch(child->child==pair.first.get(0))||pair.first.get(0).getPlacementsHolder().getPlacements().get(pair.second.getId())!=pair.second)reject("Native pseudo resolver changed the exact target");
            PlacementsHolderLocation<?> branch=pair.first.get(0);Placement leaf=pair.second;
            PlacementsHolder<?> definition=branch.getPlacementsHolder().getDefinition();
            if(definition.getPlacements().get(leaf.getId())!=leaf.getDefinition())reject("Native pseudo leaf differs from the canonical placement definition");
            // Bind the existing object's native subscriptions, not only a freshly resolved equal pose.
            for(String property:List.of("placementsHolder","location","side","id"))if(!branch.getDefinition().isListener(property,actual))reject("Native pseudo is detached from its current child definition");
            for(String property:List.of("location","side","id","part","type"))if(!leaf.getDefinition().isListener(property,actual))reject("Native pseudo is detached from its canonical leaf definition");
            if(!definition.isListener("placement",actual))reject("Native pseudo is detached from its canonical board definition");
            Placement expected=panel.createPseudoPlacement(actual.getId());try{if(!samePlacement(actual,expected))reject("Native pseudo overrides or derived geometry cannot roundtrip");}finally{expected.dispose();}pseudoIds.add(actual.getId());
        }
        if(!pseudoIds.equals(panel.getPseudoPlacementIds()))reject("Native pseudo ID list differs from resolved objects");
    }
    static void canonicalPart(Configuration c,Placement p)throws Exception {Part part=p.getPart();if(part==null||part.getClass()!=Part.class||c.getPart(part.getId())!=part||part.getPackage()==null||part.getPackage().getClass()!=org.openpnp.model.Package.class||c.getPackage(part.getPackage().getId())!=part.getPackage())reject("Noncanonical Part/Package reference");}
    static boolean samePlacement(Placement a,Placement b){Location x=a.getLocation(),y=b.getLocation();return Objects.equals(a.getId(),b.getId())&&a.getPart()==b.getPart()&&a.getType()==b.getType()&&a.getSide()==b.getSide()&&a.isEnabled()==b.isEnabled()&&a.getErrorHandling()==b.getErrorHandling()&&a.getRank()==b.getRank()&&Objects.equals(a.getComments(),b.getComments())&&x.getUnits()==y.getUnits()&&Double.compare(x.getX(),y.getX())==0&&Double.compare(x.getY(),y.getY())==0&&Double.compare(x.getZ(),y.getZ())==0&&Double.compare(x.getRotation(),y.getRotation())==0;}
    static final class Shape {int childCount,ownCount,pseudoCount,expanded;final List<Map<String,Object>> bindings=new ArrayList<>(),pseudos=new ArrayList<>();}
    static Shape validatePanel(Document doc,Map<String,Document> boards,Set<String> parts,int[] budget)throws Exception {
        Element root=doc.getDocumentElement();if(!"openpnp-panel".equals(root.getTagName()))reject("Expected native panel XML");attributes(root,Set.of("name","version"));if(!"2.0".equals(root.getAttribute("version")))reject("Only saved modern panel version2.0 is portable");noText(root);
        Set<String> fields=new HashSet<>();for(Element e:children(root)){if(!fields.add(e.getTagName())||!Set.of("dimensions","placements","children","pseudo-placement-ids").contains(e.getTagName()))reject("Unknown or duplicate panel field");}
        // Reuse exact flat-board geometry/placement validation without adding dynamic native classes.
        Document own=xml("<openpnp-board/>".getBytes(StandardCharsets.UTF_8));for(String field:List.of("dimensions","placements")){Element e=one(root,field);if(e!=null)own.getDocumentElement().appendChild(own.importNode(e,true));}
        int[] ownBudget=new int[4];NativePortableBoardLibrary.validateBoard(own,parts,ownBudget);Shape shape=new Shape();shape.ownCount=ownBudget[0];shape.expanded=shape.ownCount;
        Element placements=one(root,"placements");if(placements!=null)for(Element p:children(placements)){localId(p.getAttribute("id"));if(!"Fiducial".equals(p.getAttribute("type")))reject("Own panel records must be Fiducials");}
        Element group=one(root,"children");if(group!=null){attributes(group,Set.of());noText(group);}Map<String,Element> childById=new LinkedHashMap<>();Map<String,Map<String,Element>> placementsByChild=new LinkedHashMap<>();
        for(Element child:childElements(doc)){
            if(!"object".equals(child.getTagName()))reject("Unexpected native panel child element");attributes(child,Set.of("class","side","id","file-name","check-fiducials","locally-enabled"));noText(child);if(!"org.openpnp.model.BoardLocation".equals(child.getAttribute("class")))reject("Only exact direct native BoardLocation children are portable");String id=child.getAttribute("id");localId(id);if(childById.put(id,child)!=null)reject("Duplicate panel child ID");
            if(!Set.of("Top","Bottom").contains(child.getAttribute("side")))reject("Unsupported child side");for(String flag:List.of("check-fiducials","locally-enabled"))if(!Set.of("true","false").contains(child.getAttribute(flag)))reject("Child flags must be explicit native booleans");
            List<Element> values=children(child);if(values.size()!=1||!values.get(0).getTagName().equals("location"))reject("Child permits only native design location; no legacy history");NativePortableBoardLibrary.location(values.get(0),false);
            String ref=child.getAttribute("file-name");if(!ref.matches("codex-board:board-[0-9]{3}")||!boards.containsKey(ref.substring(12)))reject("Uninventoried logical board reference");String boardId=ref.substring(12);Document board=boards.get(boardId);Map<String,Element> lookup=new LinkedHashMap<>();Element bp=one(board.getDocumentElement(),"placements");if(bp!=null)for(Element p:children(bp)){localId(p.getAttribute("id"));lookup.put(p.getAttribute("id"),p);}placementsByChild.put(id,lookup);
            shape.childCount++;shape.expanded+=lookup.size();if(shape.childCount>MAX_HOLDERS||shape.expanded>MAX_RECORDS)reject("Panel expansion exceeds bound");shape.bindings.add(Bridge.map("child_id",id,"board_id",boardId));
        }
        Element pg=one(root,"pseudo-placement-ids");if(pg!=null){attributes(pg,Set.of());noText(pg);Set<String> unique=new HashSet<>();for(Element e:children(pg)){
            if(!"string".equals(e.getTagName())||e.hasAttributes()||!children(e).isEmpty())reject("Unsupported pseudo reference");String id=e.getTextContent();identifier(id,512);String[] split=id.split(SEP,-1);if(split.length!=2||!unique.add(id)||!placementsByChild.containsKey(split[0])||!placementsByChild.get(split[0]).containsKey(split[1]))reject("Pseudo target must be an exact direct child placement");
            String resolvedChild=null,resolvedPlacement=null;for(String child:childById.keySet())if(id.startsWith(child)){String rest=id.substring(child.length());if(rest.startsWith(SEP))rest=rest.substring(1);if(placementsByChild.get(child).containsKey(rest)){resolvedChild=child;resolvedPlacement=rest;break;}}
            if(!split[0].equals(resolvedChild)||!split[1].equals(resolvedPlacement))reject("Pinned native prefix resolver would change pseudo target");String boardId=childById.get(split[0]).getAttribute("file-name").substring(12);shape.pseudos.add(Bridge.map("pseudo_id",id,"child_id",split[0],"placement_id",split[1],"board_id",boardId));shape.pseudoCount++;shape.expanded++;
        }}
        if(shape.expanded>MAX_RECORDS)reject("Expanded pseudo budget exceeded");NativePortableBoardLibrary.walkBudget(root,budget);return shape;
    }
    static List<JsonObject> records(JsonObject m)throws Exception {
        if(!"3".equals(m.get("version").toString())||!m.has("panel_library")||!m.get("panel_library").isJsonObject())reject("Expected version3 panel-library manifest");JsonObject lib=m.getAsJsonObject("panel_library");fields(lib,Set.of("profile","panels","panel_count","child_count","placement_count","pseudo_count","expanded_record_count"));if(!PROFILE.equals(string(lib,"profile"))||!lib.get("panels").isJsonArray())reject("Unsupported panel profile");
        int count=integer(lib,"panel_count",MAX_PANELS),children=integer(lib,"child_count",MAX_HOLDERS),own=integer(lib,"placement_count",MAX_RECORDS),pseudos=integer(lib,"pseudo_count",MAX_RECORDS),expanded=integer(lib,"expanded_record_count",MAX_RECORDS);JsonArray rows=lib.getAsJsonArray("panels");if(count<1||rows.size()!=count||count+children>MAX_HOLDERS)reject("Panel count or expansion inventory mismatch");
        List<JsonObject> out=new ArrayList<>();int c=0,p=0,s=0,x=0;for(JsonElement value:rows){if(!value.isJsonObject())reject("Expected panel record");JsonObject row=value.getAsJsonObject();fields(row,Set.of("id","entry","sha256","semantic_sha256","source_sha256","child_count","placement_count","pseudo_count","expanded_record_count","board_bindings","pseudo_bindings"));String id=String.format(Locale.ROOT,"panel-%03d",out.size()+1);for(String key:List.of("sha256","semantic_sha256","source_sha256"))if(!string(row,key).matches("[a-f0-9]{64}"))reject("Invalid panel digest");if(!id.equals(string(row,"id"))||!("library/panels/"+id+"-"+string(row,"sha256")+".panel.xml").equals(string(row,"entry")))reject("Invalid generated panel identity");c+=integer(row,"child_count",MAX_HOLDERS);p+=integer(row,"placement_count",MAX_RECORDS);s+=integer(row,"pseudo_count",MAX_RECORDS);x+=integer(row,"expanded_record_count",MAX_RECORDS);if(!row.get("board_bindings").isJsonArray()||!row.get("pseudo_bindings").isJsonArray())reject("Expected closed panel binding inventories");out.add(row);}
        if(c!=children||p!=own||s!=pseudos||x!=expanded)reject("Panel cumulative count mismatch");return out;
    }
    static int integer(JsonObject o,String key,int max)throws Exception{return NativePortableBoardLibrary.integer(o,key,max);}
    static Map<String,Document> boardDocs(Map<String,byte[]> entries,JsonObject m)throws Exception {Map<String,Document> docs=new LinkedHashMap<>();for(JsonObject row:NativePortableBoardLibrary.panelBoardRecords(m))docs.put(string(row,"id"),xml(entries.get(string(row,"entry"))));return docs;}
    static void validateArchive(Map<String,byte[]> entries,JsonObject m)throws Exception {
        NativePortableBoardLibrary.validatePanelBoards(entries,m);List<JsonObject> rows=records(m);Map<String,Document> boards=boardDocs(entries,m);Set<String> parts=NativePortableBoardLibrary.partIds(entries),expected=new TreeSet<>();for(JsonObject b:NativePortableBoardLibrary.panelBoardRecords(m))expected.add(string(b,"entry"));int[] budget=new int[4];
        for(JsonObject row:rows){String entry=string(row,"entry");expected.add(entry);byte[] bytes=entries.get(entry);if(bytes==null||!sha(bytes).equals(string(row,"sha256")))reject("Missing or changed panel resource");Document d=xml(bytes);Shape shape=validatePanel(d,boards,parts,budget);verifyShape(row,shape);if(!semantic(d).equals(string(row,"semantic_sha256")))reject("Panel semantic digest differs");}
        Set<String> actual=new TreeSet<>();for(String key:entries.keySet())if(key.startsWith("library/"))actual.add(key);if(!actual.equals(expected))reject("Panel/board resource union is incomplete or contains extras");registry(xml(entries.get("config/panels.xml")),m,null,null);
    }
    static void verifyShape(JsonObject row,Shape shape)throws Exception {if(shape.childCount!=integer(row,"child_count",MAX_HOLDERS)||shape.ownCount!=integer(row,"placement_count",MAX_RECORDS)||shape.pseudoCount!=integer(row,"pseudo_count",MAX_RECORDS)||shape.expanded!=integer(row,"expanded_record_count",MAX_RECORDS)||!JSON.toJsonTree(shape.bindings).equals(row.get("board_bindings"))||!JSON.toJsonTree(shape.pseudos).equals(row.get("pseudo_bindings")))reject("Panel binding/count inventory differs from native XML");}
    /** null source means exact logical references; source means exact observed native registry paths. */
    static void registry(Document doc,JsonObject m,List<String> source,Path stage)throws Exception {
        List<JsonObject> rows=records(m);Element root=doc.getDocumentElement();if(!"openpnp-panels".equals(root.getTagName())||root.hasAttributes())reject("Invalid native panel registry");noText(root);List<Element> elements=children(root);if(elements.size()!=rows.size())reject("Panel registry incomplete or expanded");Set<String> unique=new HashSet<>();for(int i=0;i<rows.size();i++){Element e=elements.get(i);if(!"panel".equals(e.getTagName())||e.hasAttributes()||!children(e).isEmpty())reject("Invalid native panel registry member");String id=string(rows.get(i),"id"),expected=source==null?"codex-panel:"+id:source.get(i);if(!expected.equals(e.getTextContent())||!unique.add(expected))reject("Panel registry reference differs");e.setTextContent(stage==null?"codex-panel:"+id:stage.resolve(string(rows.get(i),"entry")).toString());}}
    static void rewritePanel(Document doc,JsonObject m,Path stage,boolean toNative)throws Exception {
        Map<String,String> refs=new HashMap<>();for(JsonObject b:NativePortableBoardLibrary.panelBoardRecords(m)){String logical="codex-board:"+string(b,"id"),nativePath=stage.resolve(string(b,"entry")).toString();refs.put(toNative?logical:nativePath,toNative?nativePath:logical);}for(Element child:childElements(doc)){String ref=child.getAttribute("file-name");if(!refs.containsKey(ref))reject("Panel child path differs from the exact reserved generation");child.setAttribute("file-name",refs.get(ref));}}
    static void stagePanels(Map<String,byte[]> entries,JsonObject m,Path stage)throws Exception {for(JsonObject row:records(m)){Document d=xml(entries.get(string(row,"entry")));rewritePanel(d,m,stage,true);replace(stage.resolve(string(row,"entry")),encode(d));}}
    static void collect(Map<String,byte[]> entries,JsonObject m,Path stage)throws Exception {for(JsonObject row:records(m)){Document d=xml(read(stage.resolve(string(row,"entry")),MAX_FILE));rewritePanel(d,m,stage,false);entries.put(string(row,"entry"),encode(d));}Document registry=xml(entries.get("config/panels.xml"));normalizeRegistry(registry,m,stage);entries.put("config/panels.xml",encode(registry));}
    static void normalizeRegistry(Document doc,JsonObject m,Path stage)throws Exception {List<String> paths=new ArrayList<>();for(JsonObject row:records(m))paths.add(stage.resolve(string(row,"entry")).toString());registry(doc,m,paths,null);}
    static Map<String,Object> verifyLoaded(Configuration c,JsonObject m,Path stage)throws Exception {
        List<JsonObject> rows=records(m);if(c.getPanels().size()!=rows.size())reject("Native loader skipped or added panel definitions");IdentityHashMap<Board,String> ids=new IdentityHashMap<>();List<JsonObject> boardRows=NativePortableBoardLibrary.panelBoardRecords(m);for(int i=0;i<boardRows.size();i++)ids.put(c.getBoards().get(i),string(boardRows.get(i),"id"));Map<String,byte[]> entries=new TreeMap<>();for(JsonObject b:boardRows)entries.put(string(b,"entry"),read(stage.resolve(string(b,"entry")),MAX_FILE));Map<String,Document> boards=boardDocs(entries,m);int[] budget=new int[4];
        Set<Object> unique=Collections.newSetFromMap(new IdentityHashMap<>());for(int i=0;i<rows.size();i++){Panel panel=c.getPanels().get(i);JsonObject row=rows.get(i);Path path=stage.resolve(string(row,"entry"));if(!unique.add(panel)||panel.isDirty()||panel.getFile()==null||!panel.getFile().toPath().toAbsolutePath().normalize().equals(path))reject("Loaded panel identity/file/dirty state differs");validateModel(c,panel,ids);Document d=xml(serialize(panel));rewritePanel(d,m,stage,false);verifyShape(row,validatePanel(d,boards,partIds(c),budget));if(!semantic(d).equals(string(row,"semantic_sha256")))reject("Native panel roundtrip changed fields");Document saved=xml(read(path,MAX_FILE));rewritePanel(saved,m,stage,false);if(!semantic(saved).equals(semantic(d)))reject("Loaded and saved panel definitions differ");}
        return receipt(m);
    }
    static Map<String,Object> receipt(JsonObject m)throws Exception {JsonObject lib=m.getAsJsonObject("panel_library");return Bridge.map("profile",PROFILE,"panels",integer(lib,"panel_count",MAX_PANELS),"children",integer(lib,"child_count",MAX_HOLDERS),"placements",integer(lib,"placement_count",MAX_RECORDS),"pseudo_placements",integer(lib,"pseudo_count",MAX_RECORDS),"expanded_records",integer(lib,"expanded_record_count",MAX_RECORDS),"native_registry_complete",true,"operational_authority_transferred",false);}
    static void reject(String message)throws Bridge.Fault {fail("PORTABLE_PANEL_LIBRARY",message);}
}
