/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.awt.geom.AffineTransform;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;
import org.openpnp.model.PlacementsHolderLocation.PlacementsTransformStatus;
import org.openpnp.spi.*;

/** Native save/load of actual linked Job/Board/Panel models, without a fake XML implementation. */
public final class NativeJobDocumentsTest {
    static final List<String> passed=new ArrayList<>();
    static Configuration config;static Part part;static Path root;static NativeJobDocuments documents;
    @FunctionalInterface interface Action{void run()throws Exception;}
    static void check(boolean ok,String message){if(!ok)throw new AssertionError(message);}
    static void expect(String code,Action action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){if(!code.equals(e.code))throw new AssertionError("Expected "+code+", got "+e.code,e);}}
    static Location pose(double x,double y,double z,double r){return new Location(LengthUnit.Millimeters,x,y,z,r);}
    static BoardLocation boardInstance(Board definition,String id,Side side,Location pose){BoardLocation instance=new BoardLocation(new Board(definition));instance.setId(id);instance.setSide(side);instance.setLocation(pose);return instance;}
    static PanelLocation panelInstance(Panel definition,String id,Side side,Location pose){PanelLocation instance=new PanelLocation(new Panel(definition));instance.setId(id);instance.setSide(side);instance.setLocation(pose);PanelLocation.setParentsOfAllDescendants(instance);return instance;}
    public static void main(String[] args)throws Exception {
        root=Files.createTempDirectory("openpnp-native-documents-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();
        part=config.getParts().get(0);documents=new NativeJobDocuments(config,root.resolve("documents"));int exit=0;
        try{testRoundTrip();testRejectedGraph();testReviewRegressions();check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"document actions never enable/home machine");System.out.println("OPENPNP_NATIVE_DOCUMENTS_RESULT "+new Gson().toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false)));}
        catch(Throwable e){e.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
    }
    static Job fixture() {
        Board board=new Board();board.setName("shared-definition");board.setDimensions(pose(20,10,0,0));
        Placement top=new Placement("R-top");top.setPart(part);top.setLocation(pose(3,4,0,30));top.setSide(Side.Top);top.setComments("preserve native placement metadata");top.setRank(7);board.addPlacement(top);
        Placement bottom=new Placement("R-bottom");bottom.setPart(part);bottom.setLocation(pose(7,2,0,-45));bottom.setSide(Side.Bottom);board.addPlacement(bottom);
        Panel inner=new Panel();inner.setName("inner");inner.setDimensions(pose(50,20,0,0));
        BoardLocation a=boardInstance(board,"A",Side.Top,pose(2,3,0,20));inner.addChild(a);
        BoardLocation b=boardInstance(board,"B",Side.Bottom,pose(25,3,0,-20));b.setLocallyEnabled(false);inner.addChild(b);
        Panel outer=new Panel();outer.setName("outer");outer.setDimensions(pose(60,60,0,0));outer.addChild(panelInstance(inner,"nested",Side.Top,pose(5,6,1,90)));
        Job job=new Job();job.addBoardOrPanelLocation(boardInstance(board,"top",Side.Top,pose(100,120,3,15)));job.addBoardOrPanelLocation(boardInstance(board,"bottom",Side.Bottom,pose(150,120,3,-15)));job.addBoardOrPanelLocation(panelInstance(outer,"panel",Side.Bottom,pose(200,100,3,180)));
        PanelLocation.setParentsOfAllDescendants(job.getRootPanelLocation());
        job.storePlacedStatus(job.getBoardLocations().get(0),"R-top",true);
        job.getBoardLocations().get(2).getBoard().getPlacements().get("R-bottom").setEnabled(false);
        job.getBoardLocations().get(2).getBoard().getPlacements().get("R-top").setErrorHandling(Placement.ErrorHandling.Defer);
        job.setDirty(true);return job;
    }
    static Map<String,Object> intent(Job job) {
        Map<String,Object> result=new LinkedHashMap<>();
        for(PlacementsHolderLocation<?> l:job.getBoardAndPanelLocations()){
            if(l==job.getRootPanelLocation())continue;
            List<Object> placements=new ArrayList<>();for(Placement p:l.getPlacementsHolder().getPlacements())placements.add(Arrays.asList(p.getId(),p.getPart()==null?null:p.getPart().getId(),p.getLocation().toString(),p.getSide().name(),p.isEnabled(),p.getErrorHandling().name(),p.getComments(),p.getRank(),job.retrievePlacedStatus(l,p.getId())));
            result.put(l.getUniqueId(),Arrays.asList(l.getClass().getSimpleName(),l.getPlacementsHolder().getName(),l.getLocation().toString(),l.getSide().name(),l.getGlobalSide().name(),l.isLocallyEnabled(),l.isEnabled(),placements));
        }
        return result;
    }
    static void testRoundTrip()throws Exception {
        Job original=fixture();Map<String,Object> before=intent(original);Map<Object,Object> fileMetadata=new IdentityHashMap<>();
        for(PlacementsHolderLocation<?> l:original.getBoardAndPanelLocations()){fileMetadata.put(l,l.getFileName());fileMetadata.put(l.getPlacementsHolder(),l.getPlacementsHolder().getFile());}
        // Upstream does not serialize calibrated transient registration. Static pose remains the source.
        original.getBoardLocations().get(0).setLocalToGlobalTransform(AffineTransform.getTranslateInstance(9,9));
        NativeJobDocuments.Saved saved=documents.save(original);
        check(saved.sha256.matches("[a-f0-9]{64}")&&saved.bytes.length>100,"native bundle has content address and ZIP bytes");
        check(Boolean.TRUE.equals(saved.manifest.get("shared_definitions_preserved"))&&Boolean.FALSE.equals(saved.manifest.get("transient_registration_preserved")),"manifest states sharing and transient registration limits");
        check(original.getFile()==null&&original.isDirty(),"export restores native job file and dirty metadata");
        check(before.equals(intent(original)),"export preserves native job intent and placed history");
        for(PlacementsHolderLocation<?> l:original.getBoardAndPanelLocations()){check(Objects.equals(fileMetadata.get(l),l.getFileName()),"location reference restored");check(Objects.equals(fileMetadata.get(l.getPlacementsHolder()),l.getPlacementsHolder().getFile()),"native instance file restored");}
        int boards=0,panels=0;try(ZipInputStream zip=new ZipInputStream(new ByteArrayInputStream(saved.bytes))){ZipEntry e;while((e=zip.getNextEntry())!=null){check(!e.getName().contains("/")&&!e.getName().contains(".."),"flat generated asset paths");if(e.getName().endsWith(".board.xml"))boards++;if(e.getName().endsWith(".panel.xml"))panels++;}}
        check(boards==1&&panels==2,"bundle deduplicates one shared board and retains two nested panel definitions");
        Job loaded=documents.reload(saved.sha256);check(before.equals(intent(loaded)),"native reload preserves top/bottom/nested poses, X-outs, overrides, metadata and placed history");
        Board definition=loaded.getBoardLocations().get(0).getBoard().getDefinition();for(BoardLocation b:loaded.getBoardLocations())check(b.getBoard().getDefinition()==definition,"reloaded instances retain one shared native board definition");
        check(loaded.getBoardLocations().get(0).getPlacementsTransformStatus()==PlacementsTransformStatus.NotSet,"reloaded job requires fresh native vision registration");
        passed.add("native shared board/nested panel ZIP round-trip preserves sides, X-outs, overrides, metadata and placed history while dropping transient registration");
        Length height=part.getHeight();part.setHeight(new Length(height.convertToUnits(LengthUnit.Millimeters).getValue()+0.01,LengthUnit.Millimeters));expect("DOCUMENT_DEPENDENCY_CHANGED",()->documents.reload(saved.sha256));part.setHeight(height);
        org.openpnp.model.Package pkg=part.getPackage();String description=pkg.getDescription();pkg.setDescription("changed dependency");expect("DOCUMENT_DEPENDENCY_CHANGED",()->documents.reload(saved.sha256));pkg.setDescription(description);
        check(before.equals(intent(documents.reload(saved.sha256))),"unchanged dependencies allow fresh reload");
        passed.add("native part/package dependency fingerprints reject changed definitions before reload");
        Path retained=root.resolve("documents").resolve(saved.sha256+".zip");byte[] corrupt=saved.bytes.clone();corrupt[corrupt.length/2]^=1;Files.write(retained,corrupt);expect("ARTIFACT_INTEGRITY",()->documents.reload(saved.sha256));Files.write(retained,saved.bytes);
        expect("DOCUMENT_NOT_FOUND",()->documents.reload("0".repeat(64)));expect("DOCUMENT_NOT_FOUND",()->documents.reload("../job.job.xml"));
        NativeJobDocuments otherInstance=new NativeJobDocuments(config,root.resolve("other-documents"));expect("DOCUMENT_NOT_FOUND",()->otherInstance.reload(saved.sha256));
        passed.add("tampered retained bytes, arbitrary paths, unknown hashes and another helper instance are rejected before native XML loading");
    }
    static void testRejectedGraph()throws Exception {
        Job job=fixture();BoardLocation target=job.getBoardLocations().get(0);target.getBoard().getPlacements().get("R-top").setLocation(pose(99,99,0,0));
        expect("UNSUPPORTED_INSTANCE_EDIT",()->documents.save(job));check(job.getFile()==null&&target.getFileName()==null,"unsupported instance edit rejected before file rebinding");
        Job custom=new Job();custom.addBoardOrPanelLocation(new BoardLocation(new CustomBoard()));expect("UNSUPPORTED_NATIVE_DOCUMENT",()->documents.save(custom));
        expect("PATH_REJECTED",()->new NativeJobDocuments(config,root.resolveSibling("outside-document-root")));
        passed.add("unsupported instance drift, custom native classes and outside staging roots are rejected before persistence");
        Panel sharedPanel=new Panel();sharedPanel.setName("expanded-fiducials");sharedPanel.setDimensions(pose(20,20,0,0));
        for(int i=0;i<100;i++){Placement fiducial=new Placement("FID-"+i);fiducial.setType(Placement.Type.Fiducial);fiducial.setPart(part);fiducial.setLocation(pose(i%10,i/10,0,0));sharedPanel.addPlacement(fiducial);}
        Job expanded=new Job();for(int i=0;i<101;i++){PanelLocation instance=new PanelLocation(sharedPanel);instance.setId("panel-"+i);expanded.addBoardOrPanelLocation(instance);}
        expect("DOCUMENT_GRAPH_LIMIT",()->documents.save(expanded));check(expanded.getFile()==null,"expanded shared panel fiducial count rejected before rebinding");
        passed.add("independent review regression: repeated shared panel placement/fiducial records count toward the expanded native graph limit");
    }
    // Retained repro cases supplied by the independent native reviewer, not removed when they fail.
    static Job nestedReviewFixture(boolean pseudo)throws Exception {
        Board board=new Board();board.setName("review-board");board.setDimensions(pose(20,10,0,0));
        Placement fid=new Placement("FID");fid.setPart(part);fid.setType(Placement.Type.Fiducial);fid.setLocation(pose(2,2,0,0));board.addPlacement(fid);
        BoardLocation child=new BoardLocation(new Board(board));child.setId("A");Panel panel=new Panel();panel.setName("review-panel");panel.setDimensions(pose(20,10,0,0));panel.addChild(child);
        if(pseudo)panel.addPseudoPlacement(panel.createPseudoPlacement("A"+PlacementsHolderLocation.ID_DELIMITTER+"FID"));
        PanelLocation copy=new PanelLocation(new Panel(panel));copy.setId("panel");Job job=new Job();job.addBoardOrPanelLocation(copy);PanelLocation.setParentsOfAllDescendants(job.getRootPanelLocation());return job;
    }
    static void testReviewRegressions()throws Exception {
        Job pseudo=nestedReviewFixture(true);Panel before=(Panel)pseudo.getRootPanelLocation().getChildren().get(0).getPlacementsHolder();check(before.getPseudoPlacements().size()==1,"review pseudo fixture contains native pseudo-fiducial");
        Job pseudoLoaded=documents.reload(documents.save(pseudo).sha256);Panel after=(Panel)pseudoLoaded.getRootPanelLocation().getChildren().get(0).getPlacementsHolder();
        check(after.getPseudoPlacements().size()==1,"pseudo-fiducial survives serializer-clone and native save/load");
        Placement expected=before.getPseudoPlacements().get(0),actual=after.getPseudoPlacements().get(0);check(actual.getId().equals(expected.getId())&&actual.getPart()==expected.getPart()&&actual.getLocation().equals(expected.getLocation())&&actual.getSide()==expected.getSide(),"pseudo-fiducial identity, part, position and side survive");
        passed.add("independent review regression: linked native panel pseudo-fiducial persists and resolves after reload");
        Job pseudoDrift=nestedReviewFixture(true);Panel pseudoInstance=(Panel)pseudoDrift.getRootPanelLocation().getChildren().get(0).getPlacementsHolder();Placement pseudoPlacement=pseudoInstance.getPseudoPlacements().get(0);Location pseudoLocation=pseudoPlacement.getLocation();
        pseudoPlacement.setLocation(pose(99,99,0,0));expect("UNSUPPORTED_INSTANCE_EDIT",()->documents.save(pseudoDrift));check(pseudoDrift.getFile()==null,"pseudo-placement geometry drift rejected before rebinding");
        pseudoPlacement.setLocation(pseudoLocation);pseudoPlacement.setEnabled(!pseudoPlacement.isEnabled());expect("UNSUPPORTED_INSTANCE_EDIT",()->documents.save(pseudoDrift));check(pseudoDrift.getFile()==null,"pseudo-placement enable drift rejected before rebinding");
        passed.add("independent review regression: instance-only pseudo-placement geometry and enabled edits are rejected before persistence");
        Job flags=nestedReviewFixture(false);BoardLocation child=flags.getBoardLocations().get(0);child.setCheckFiducials(true);NativeJobDocuments.Saved checked=documents.save(flags);check(documents.reload(checked.sha256).getBoardLocations().get(0).isCheckFiducials(),"true fiducial override persists");
        child.setCheckFiducials(false);Job unchecked=documents.reload(documents.save(flags).sha256);check(!unchecked.getBoardLocations().get(0).isCheckFiducials(),"stale true fiducial override cannot resurrect after false save");
        passed.add("independent review regression: check-fiducials true -> false across native saves does not restore stale true");
        Job outline=nestedReviewFixture(false);Board instance=outline.getBoardLocations().get(0).getBoard();GeometricPath2D profile=instance.getProfile();profile.reset();profile.moveTo(0,0);profile.lineTo(7,0);profile.lineTo(0,5);profile.closePath();instance.setProfile(profile);
        expect("UNSUPPORTED_INSTANCE_EDIT",()->documents.save(outline));check(instance.getProfile().getBounds2D().getWidth()==7&&outline.getFile()==null&&outline.getBoardLocations().get(0).getFileName()==null,"instance outline drift rejected before native persistence mutation");
        passed.add("independent review regression: instance-only outline edit is rejected before mutation rather than silently replaced");
        Job rootPseudo=nestedReviewFixture(false);Panel nativeRoot=rootPseudo.getRootPanelLocation().getPanel();
        String pseudoId="panel"+PlacementsHolderLocation.ID_DELIMITTER+"A"+PlacementsHolderLocation.ID_DELIMITTER+"FID";
        nativeRoot.addPseudoPlacement(nativeRoot.createPseudoPlacement(pseudoId));
        check(nativeRoot.getPseudoPlacements().size()==1,"root pseudo regression uses an actual native root pseudo fiducial");
        expect("UNSUPPORTED_NATIVE_DOCUMENT",()->documents.save(rootPseudo));
        check(rootPseudo.getFile()==null&&nativeRoot.getPseudoPlacements().size()==1,"unsupported root pseudo state remains unchanged");
        Job rootIds=nestedReviewFixture(false);Panel idsRoot=rootIds.getRootPanelLocation().getPanel();
        idsRoot.setPseudoPlacementIds(new ArrayList<>(Arrays.asList(pseudoId)));
        check(idsRoot.getPseudoPlacements().isEmpty(),"persisted root-ID fixture has no reconstructed transient pseudo placement");
        expect("UNSUPPORTED_NATIVE_DOCUMENT",()->documents.save(rootIds));
        check(rootIds.getFile()==null&&idsRoot.getPseudoPlacementIds().equals(Arrays.asList(pseudoId)),"unreconstructed root IDs remain intact after rejection");
        Job rootOutline=nestedReviewFixture(false);Panel outlineRoot=rootOutline.getRootPanelLocation().getPanel();
        GeometricPath2D rootProfile=outlineRoot.getProfile();rootProfile.reset();rootProfile.moveTo(0,0);rootProfile.lineTo(9,0);rootProfile.lineTo(0,6);rootProfile.closePath();outlineRoot.setProfile(rootProfile);
        expect("UNSUPPORTED_NATIVE_DOCUMENT",()->documents.save(rootOutline));
        check(rootOutline.getFile()==null&&outlineRoot.getProfile().getBounds2D().getWidth()==9,"unsupported root profile remains unchanged");
        passed.add("independent review regression: inline root pseudo-fiducials and explicit outlines are rejected before native serialization can drop or corrupt them");
        // Pinned native definition profile serialization/copy has separate upstream limitations;
        // keep explicit coverage of the bounded rejection instead of silently losing its shape.
        Board shared=new Board();shared.setName("custom-outline-definition");shared.setDimensions(pose(20,10,0,0));GeometricPath2D sharedProfile=shared.getProfile();sharedProfile.reset();sharedProfile.moveTo(0,0);sharedProfile.lineTo(9,0);sharedProfile.lineTo(0,6);sharedProfile.closePath();shared.setProfile(sharedProfile);
        BoardLocation sharedInstance=new BoardLocation(new Board(shared));sharedInstance.setId("custom");sharedInstance.getBoard().setProfile(sharedProfile.convertToUnits(LengthUnit.Millimeters));Job sharedOutline=new Job();sharedOutline.addBoardOrPanelLocation(sharedInstance);
        expect("UNSUPPORTED_NATIVE_DOCUMENT",()->documents.save(sharedOutline));check(sharedOutline.getFile()==null&&shared.getProfile().getBounds2D().getWidth()==9,"custom definition outline rejected before persistence");
        passed.add("explicit shared definition outlines are rejected as a pinned-native limitation before persistence");
    }
    static final class CustomBoard extends Board { }
}
