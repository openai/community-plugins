/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.w3c.dom.*;
import static org.openpnp.codex.NativePortableConfiguration.*;

/** Saved exact native Board definitions only. No jobs, loads, history, or authority are adopted. */
public final class NativePortableBoardLibrary {
    static final int MAX_BOARDS=32,MAX_PLACEMENTS=10000,MAX_PADS=10000,MAX_ELEMENTS=50000,MAX_TEXT=2*1024*1024;
    static final String PROFILE="saved-flat-board-library-v1";
    private NativePortableBoardLibrary(){}
    public static Map<String,Object> describe(){return Bridge.map("profile",PROFILE,"boards",MAX_BOARDS,"placements_total",MAX_PLACEMENTS,"paste_pads_total",MAX_PADS,"paste_pad_classes",List.of("org.openpnp.model.Pad$RoundRectangle"),"paste_execution",false,"xml_elements_total",MAX_ELEMENTS,"xml_text_total",MAX_TEXT,"source","saved-clean-exact-native-Board","panels",false,"custom_outlines",false,"job_documents",false,"operational_history",false,"physical_authority",false);}
    static final class Captured {
        final Map<String,byte[]> entries=new TreeMap<>();final List<Map<String,Object>> records=new ArrayList<>();final List<String> sourcePaths=new ArrayList<>();
        Map<String,Object> manifest(){return Bridge.map("profile",PROFILE,"boards",records,"board_count",records.size(),"placement_count",records.stream().mapToInt(x->((Number)x.get("placement_count")).intValue()).sum(),"paste_pad_count",records.stream().mapToInt(x->((Number)x.get("paste_pad_count")).intValue()).sum());}
    }
    static Captured capture(Configuration c)throws Exception {
        if(!c.getPanels().isEmpty())fail("LIBRARY_NOT_SUPPORTED","Panel libraries are outside the saved flat-board profile");
        return captureBoards(c);
    }
    static Captured captureForPanels(Configuration c)throws Exception {return captureBoards(c);}
    private static Captured captureBoards(Configuration c)throws Exception {
        List<Board> boards=c.getBoards();if(boards.size()>MAX_BOARDS)reject("Board count exceeds32");
        Captured result=new Captured();Set<Path> paths=new HashSet<>();Set<Board> identities=Collections.newSetFromMap(new IdentityHashMap<>());int[] budget=new int[4];Set<String> parts=new HashSet<>();for(Part p:c.getParts())parts.add(p.getId());
        for(Board board:boards){
            if(board.getClass()!=Board.class||board.getDefinition()!=board||!identities.add(board))reject("Only distinct exact native Board definitions are portable");
            if(board.isDirty()||board.getFile()==null)fail("LIBRARY_DIRTY","Save each board before portable export; no save dialog is invoked");
            Path file=board.getFile().toPath().toAbsolutePath().normalize();checkAncestors(file);if(!paths.add(file.toRealPath()))reject("Duplicate canonical board file identity");
            byte[] saved=read(file,MAX_FILE);Document savedDoc=xml(saved);validateBoard(savedDoc,parts,budget);validateModel(c,board);
            // Serialize the original to preserve explicit profile/legacy-field detection; Board(copy) drops profile.
            byte[] current=serialize(board);Document currentDoc=xml(current);validateBoard(currentDoc,parts,new int[4]);
            Board normalized=null;
            try {
                normalized=Configuration.createSerializer().read(Board.class,new ByteArrayInputStream(saved));validateModel(c,normalized);
                if(!semantic(xml(serialize(normalized))).equals(semantic(currentDoc)))fail("LIBRARY_CHANGED","Saved board bytes do not match the current native definition");
            }finally{if(normalized!=null)normalized.dispose();}
            String id=String.format(Locale.ROOT,"board-%03d",result.records.size()+1),digest=sha(current),entry="library/boards/"+id+"-"+digest+".board.xml";
            result.entries.put(entry,current);result.sourcePaths.add(file.toString());result.records.add(Bridge.map("id",id,"entry",entry,"sha256",digest,"semantic_sha256",semantic(currentDoc),"placement_count",board.getPlacements().size(),"paste_pad_count",board.getSolderPastePads().size()));
        }
        // Loading a saved native board can update the in-memory registry before boards.xml is saved.
        // Export persists Configuration only after these board-content checks, then verifies exact registry keys.
        return result;
    }
    static void validateModel(Configuration c,Board board)throws Exception {
        if(board.getClass()!=Board.class)reject("Unsupported native board model");
        for(BoardPad pad:board.getSolderPastePads())if(pad==null||pad.getClass()!=BoardPad.class||pad.getPad()==null||pad.getPad().getClass()!=Pad.RoundRectangle.class)reject("Unsupported native paste pad model");
        Set<String> ids=new HashSet<>();
        for(Placement p:board.getPlacements()){
            if(p.getClass()!=Placement.class||p.getId()==null||!ids.add(p.getId()))reject("Only exact native placements with unique IDs are portable");
            Part part=p.getPart();if(part==null||c.getPart(part.getId())!=part||part.getClass()!=Part.class||part.getPackage()==null||c.getPackage(part.getPackage().getId())!=part.getPackage()||part.getPackage().getClass()!=org.openpnp.model.Package.class)reject("Board placement must resolve exact canonical Part and Package");
        }
    }
    static byte[] serialize(Board board)throws Exception {ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(board,out);byte[] bytes=out.toByteArray();if(bytes.length>MAX_FILE)reject("Serialized board exceeds file limit");return bytes;}
    static List<JsonObject> records(JsonObject manifest)throws Exception {return records(manifest,false);}
    static List<JsonObject> panelBoardRecords(JsonObject manifest)throws Exception {return records(manifest,true);}
    private static List<JsonObject> records(JsonObject manifest,boolean panels)throws Exception {
        String version=manifest.has("version")?manifest.get("version").toString():"";
        if(!panels&&"1".equals(version)){if(manifest.has("board_library"))reject("Version1 cannot declare a board library");return List.of();}
        if(!(panels?"3":"2").equals(version)||!manifest.has("board_library")||!manifest.get("board_library").isJsonObject())reject("Expected version2 board-library manifest");
        JsonObject lib=manifest.getAsJsonObject("board_library");fields(lib,Set.of("profile","boards","board_count","placement_count","paste_pad_count"));if(!PROFILE.equals(string(lib,"profile"))||!lib.get("boards").isJsonArray())reject("Unknown board-library profile");
        JsonArray rows=lib.getAsJsonArray("boards");int count=integer(lib,"board_count",MAX_BOARDS),placements=integer(lib,"placement_count",MAX_PLACEMENTS),pads=integer(lib,"paste_pad_count",MAX_PADS);if((!panels&&count<1)||rows.size()!=count)reject("Board count/inventory mismatch");
        List<JsonObject> result=new ArrayList<>();int total=0,padTotal=0;
        for(JsonElement row:rows){if(!row.isJsonObject())reject("Expected board manifest record");JsonObject item=row.getAsJsonObject();fields(item,Set.of("id","entry","sha256","semantic_sha256","placement_count","paste_pad_count"));String id=String.format(Locale.ROOT,"board-%03d",result.size()+1),digest=string(item,"sha256");if(!digest.matches("[a-f0-9]{64}")||!string(item,"semantic_sha256").matches("[a-f0-9]{64}")||!id.equals(string(item,"id"))||!("library/boards/"+id+"-"+digest+".board.xml").equals(string(item,"entry")))reject("Invalid or duplicate generated board identity");total+=integer(item,"placement_count",MAX_PLACEMENTS);padTotal+=integer(item,"paste_pad_count",MAX_PADS);if(padTotal>MAX_PADS)reject("Cumulative paste pad limit");if(total>MAX_PLACEMENTS)reject("Cumulative placement limit");result.add(item);}
        if(total!=placements||padTotal!=pads)reject("Manifest placement/paste pad count mismatch");return result;
    }
    static int integer(JsonObject o,String field,int max)throws Exception {JsonElement v=o.get(field);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isNumber()||!v.getAsString().matches("0|[1-9][0-9]{0,5}"))reject("Expected exact integer "+field);int n=v.getAsInt();if(n>max)reject("Count exceeds bound");return n;}
    static void validateArchive(Map<String,byte[]> entries,JsonObject manifest)throws Exception {
        validateBoardArchive(entries,records(manifest),false);
    }
    static void validatePanelBoards(Map<String,byte[]> entries,JsonObject manifest)throws Exception {validateBoardArchive(entries,panelBoardRecords(manifest),true);}
    private static void validateBoardArchive(Map<String,byte[]> entries,List<JsonObject> rows,boolean panels)throws Exception {
        Set<String> expected=new TreeSet<>(),parts=rows.isEmpty()?Set.of():partIds(entries);int[] budget=new int[4];
        for(JsonObject row:rows){String entry=string(row,"entry");expected.add(entry);byte[] bytes=entries.get(entry);if(bytes==null||!sha(bytes).equals(string(row,"sha256")))reject("Missing or changed board resource");Document doc=xml(bytes);int before=budget[0],padsBefore=budget[3];validateBoard(doc,parts,budget);if(budget[0]-before!=integer(row,"placement_count",MAX_PLACEMENTS)||budget[3]-padsBefore!=integer(row,"paste_pad_count",MAX_PADS)||!semantic(doc).equals(string(row,"semantic_sha256")))reject("Board semantics/count mismatch");}
        Set<String> actual=new TreeSet<>();for(String name:entries.keySet())if(name.startsWith(panels?"library/boards/":"library/"))actual.add(name);if(!actual.equals(expected))reject("Unreferenced or missing board resource");
        Document registry=xml(entries.get("config/boards.xml"));rewriteRegistry(registry,null,rows,true,null);
    }
    static Set<String> partIds(Map<String,byte[]> entries)throws Exception {Set<String> ids=new HashSet<>(),packages=new HashSet<>();Document packagesDoc=xml(entries.get("config/packages.xml"));for(Element e:children(packagesDoc.getDocumentElement()))if("package".equals(e.getTagName())){String id=e.getAttribute("id");if(id.isEmpty()||!packages.add(id))reject("Duplicate or missing configured package identity");}Document d=xml(entries.get("config/parts.xml"));for(Element e:children(d.getDocumentElement())){if("part".equals(e.getTagName())){String id=e.getAttribute("id");if(id.isEmpty()||!ids.add(id)||!packages.contains(e.getAttribute("package-id")))reject("Duplicate part or unknown exact package identity");}}return ids;}
    static void stageRegistry(Document doc,JsonObject manifest,Path stage)throws Exception {rewriteRegistry(doc,null,records(manifest),true,stage);}
    static void normalizeRegistry(Document doc,JsonObject manifest,Path stage)throws Exception {List<JsonObject> rows=records(manifest);List<String> paths=new ArrayList<>();for(JsonObject row:rows)paths.add(stage.resolve(string(row,"entry")).toString());rewriteRegistry(doc,paths,rows,false,null);}
    static void captureRegistry(Document doc,Captured library)throws Exception {rewriteRegistry(doc,library.sourcePaths,library.records,false,null);}
    static void rewriteRegistry(Document doc,List<String> inputPaths,List<?> rows,boolean logical,Path stage)throws Exception {
        Element root=doc.getDocumentElement();if(!"openpnp-boards".equals(root.getTagName())||root.hasAttributes())reject("Unexpected native board registry");List<Element> boards=children(root);if(boards.size()!=rows.size())reject("Native board registry is incomplete or contains extra references");Set<String> references=new HashSet<>();
        for(int i=0;i<boards.size();i++){Element e=boards.get(i);if(!"board".equals(e.getTagName())||e.hasAttributes()||!children(e).isEmpty())reject("Unsupported native board registry entry");String id=String.format(Locale.ROOT,"board-%03d",i+1),value=e.getTextContent();String expected=logical?"codex-board:"+id:inputPaths.get(i);if(!value.equals(expected)||!references.add(value))reject("Unexpected or duplicate board registry reference");String out="codex-board:"+id;if(stage!=null){Object row=rows.get(i);String entry=row instanceof JsonObject?string((JsonObject)row,"entry"):(String)((Map<?,?>)row).get("entry");out=stage.resolve(entry).toString();}e.setTextContent(out);}
    }
    static Map<String,Object> verifyLoaded(Configuration c,JsonObject manifest,Path stage)throws Exception {
        if(!c.getPanels().isEmpty())reject("Native loader skipped or added library definitions");return verifyBoards(c,records(manifest),stage);
    }
    static Map<String,Object> verifyPanelBoards(Configuration c,JsonObject manifest,Path stage)throws Exception {return verifyBoards(c,panelBoardRecords(manifest),stage);}
    private static Map<String,Object> verifyBoards(Configuration c,List<JsonObject> rows,Path stage)throws Exception {
        if(c.getBoards().size()!=rows.size())reject("Native loader skipped or added library definitions");int count=0,pads=0;
        for(int i=0;i<rows.size();i++){JsonObject row=rows.get(i);Board board=c.getBoards().get(i);Path expected=stage.resolve(string(row,"entry"));if(board.getFile()==null||!board.getFile().toPath().toAbsolutePath().normalize().equals(expected))reject("Loaded native board reference differs");checkAncestors(expected);validateModel(c,board);if(board.isDirty()||board.getPlacements().size()!=integer(row,"placement_count",MAX_PLACEMENTS)||board.getSolderPastePads().size()!=integer(row,"paste_pad_count",MAX_PADS)||!semantic(xml(serialize(board))).equals(string(row,"semantic_sha256")))reject("Native board roundtrip changed definition");count+=board.getPlacements().size();pads+=board.getSolderPastePads().size();}
        return Bridge.map("profile",PROFILE,"boards",rows.size(),"placements",count,"paste_pad_count",pads,"native_registry_complete",true,"operational_authority_transferred",false);
    }
    static void verifyFirstLaunch(Configuration c,Path config)throws Exception {Path stage=config.getParent();JsonObject manifest=object(read(stage.resolve("manifest.json"),256*1024));verifyLoaded(c,manifest,stage);}
    static void collectActivated(Map<String,byte[]> entries,JsonObject manifest,Path stage)throws Exception {for(JsonObject row:records(manifest)){String entry=string(row,"entry");entries.put(entry,read(stage.resolve(entry),MAX_FILE));}Document registry=xml(entries.get("config/boards.xml"));normalizeRegistry(registry,manifest,stage);entries.put("config/boards.xml",encode(registry));validateArchive(entries,manifest);}
    static void validateBoard(Document doc,Set<String> parts,int[] budget)throws Exception {
        Element root=doc.getDocumentElement();if(!"openpnp-board".equals(root.getTagName()))reject("Expected native board XML");attributes(root,Set.of("name","version"));noText(root);if(root.hasAttribute("version")&&!Set.of("1.0","1.1").contains(root.getAttribute("version")))reject("Unsupported native board version");
        Set<String> seen=new HashSet<>(),ids=new HashSet<>();List<Element> children=children(root);
        for(Element e:children){String tag=e.getTagName();if(!seen.add(tag))reject("Duplicate board field");switch(tag){
        case "dimensions":location(e,true);break;
        case "outline":attributes(e,Set.of("units"));if(!children(e).isEmpty()||!e.getTextContent().trim().isEmpty())reject("Custom legacy outline unsupported");break;
        case "fiducials":attributes(e,Set.of());if(!children(e).isEmpty()||!e.getTextContent().trim().isEmpty())reject("Legacy fiducials/paste fields unsupported");break;
        case "solder-paste-pads":attributes(e,Set.of());noText(e);for(Element p:children(e)){pastePad(p);if(++budget[3]>MAX_PADS)reject("Total paste pad count exceeds10000");}break;
        case "placements":attributes(e,Set.of());noText(e);for(Element p:children(e)){
            if(!"placement".equals(p.getTagName()))reject("Unexpected placement member");noText(p);attributes(p,Set.of("version","id","side","part-id","type","enabled","rank"));String id=p.getAttribute("id");if(id.isEmpty()||id.length()>128||!ids.add(id)||!parts.contains(p.getAttribute("part-id")))reject("Duplicate placement or unknown exact part reference");if(p.hasAttribute("version")&&!Set.of("1.0","1.1","1.2","1.3","1.4").contains(p.getAttribute("version")))reject("Unsupported placement version");if(!Set.of("Top","Bottom").contains(p.getAttribute("side"))||!Set.of("Placement","Place","Ignore","Fiducial").contains(p.getAttribute("type")))reject("Unsupported placement side/type");if(p.hasAttribute("enabled")&&!Set.of("true","false").contains(p.getAttribute("enabled")))reject("Nonboolean placement enabled");if(p.hasAttribute("rank"))NativePortableLimits.integer(p.getAttribute("rank"),-10000,10000,"placement rank");Set<String> childNames=new HashSet<>();for(Element field:children(p)){if(!childNames.add(field.getTagName()))reject("Duplicate placement field");if("location".equals(field.getTagName()))location(field,false);else if("comments".equals(field.getTagName())){attributes(field,Set.of());if(!children(field).isEmpty()||field.getTextContent().length()>8192)reject("Unsupported comment");}else if("error-handling".equals(field.getTagName())){attributes(field,Set.of());if(!children(field).isEmpty()||!Set.of("Default","Alert","Defer").contains(field.getTextContent()))reject("Unsupported error handling");}else reject("Unsupported placement field");}if(!childNames.contains("location"))reject("Placement location required");if(++budget[0]>MAX_PLACEMENTS)reject("Total placement count exceeds10000");}break;
        default:reject("Unsupported board field, including custom profile: "+tag);
        }}
        walkBudget(root,budget);
    }
    static void pastePad(Element e)throws Exception {
        if(!"board-pad".equals(e.getTagName()))reject("Unexpected paste pad member");attributes(e,Set.of("type","side","name"));noText(e);
        if(!Set.of("Top","Bottom").contains(e.getAttribute("side"))||(e.hasAttribute("type")&&!Set.of("Paste","Ignore").contains(e.getAttribute("type")))||(e.hasAttribute("name")&&e.getAttribute("name").length()>128))reject("Unsupported paste pad identity/type/side");
        Set<String> seen=new HashSet<>();for(Element field:children(e)){if(!seen.add(field.getTagName()))reject("Duplicate paste pad field");if("location".equals(field.getTagName()))location(field,false);else if("pad".equals(field.getTagName())){
            attributes(field,Set.of("class","units","width","height","roundness"));if(!"org.openpnp.model.Pad$RoundRectangle".equals(field.getAttribute("class"))||!children(field).isEmpty()||!field.getTextContent().trim().isEmpty())reject("Only exact native RoundRectangle paste pads are supported");
            double factor=NativePortableLimits.units(field.getAttribute("units"));for(String key:List.of("width","height")){if(!field.hasAttribute(key))reject("Paste pad dimensions required");double value=NativePortableLimits.number(field,key,0,0,1000000)*factor;if(!Double.isFinite(value)||value>1000)reject("Paste pad geometry exceeds bounded profile");}NativePortableLimits.number(field,"roundness",0,0,1);
        }else reject("Unsupported paste pad field");}if(!seen.equals(Set.of("location","pad")))reject("Paste pad location and geometry required");
    }
    static void location(Element e,boolean dimensions)throws Exception {attributes(e,Set.of("units","x","y","z","rotation"));if(!children(e).isEmpty()||!e.getTextContent().trim().isEmpty())reject("Unexpected location content");double factor=NativePortableLimits.units(e.getAttribute("units"));for(String k:List.of("x","y","z","rotation")){double value=NativePortableLimits.number(e,k,0,-1000000,1000000)*(k.equals("rotation")?1:factor);double min=dimensions?0:k.equals("rotation")?-360000:-1000,max=k.equals("rotation")?360000:1000;if(!Double.isFinite(value)||value<min||value>max||(dimensions&&k.equals("rotation")&&value!=0))reject("Board location exceeds bounded profile");}}
    static void attributes(Element e,Set<String> allowed)throws Exception {for(int i=0;i<e.getAttributes().getLength();i++){Node a=e.getAttributes().item(i);if(!allowed.contains(a.getNodeName())||a.getNodeValue().length()>8192)reject("Unsupported board attribute "+a.getNodeName());}}
    static List<Element> children(Element e)throws Exception {List<Element> result=new ArrayList<>();for(Node n=e.getFirstChild();n!=null;n=n.getNextSibling())if(n instanceof Element)result.add((Element)n);else if(n.getNodeType()!=Node.TEXT_NODE&&n.getNodeType()!=Node.COMMENT_NODE)reject("Unsupported XML node");return result;}
    static void noText(Element e)throws Exception {for(Node n=e.getFirstChild();n!=null;n=n.getNextSibling())if(n.getNodeType()==Node.TEXT_NODE&&!n.getTextContent().trim().isEmpty())reject("Unexpected board container text");}
    static void walkBudget(Element e,int[] budget)throws Exception {if(++budget[1]>MAX_ELEMENTS)reject("Cumulative board XML element budget");for(int i=0;i<e.getAttributes().getLength();i++)budget[2]+=e.getAttributes().item(i).getNodeValue().length();for(Node n=e.getFirstChild();n!=null;n=n.getNextSibling())if(n instanceof Element)walkBudget((Element)n,budget);else budget[2]+=n.getTextContent().length();if(budget[2]>MAX_TEXT)reject("Cumulative board XML text budget");}
    static void reject(String message)throws Bridge.Fault {fail("PORTABLE_BOARD_LIBRARY",message);}
}
