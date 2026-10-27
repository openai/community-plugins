/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.SwingUtilities;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.driver.NullDriver;

/** Native settings round-trip and process-local GUI claim. No MainFrame or recovery qualification. */
public final class NativeGuiSensingFixtureTest {
    static int checks;
    interface Checked {void run()throws Exception;}
    static void yes(boolean value,String label){checks++;if(!value)throw new AssertionError(label);}
    static void refused(Checked body)throws Exception {
        try{body.run();throw new AssertionError("Unexpected fixture admission");}
        catch(java.io.IOException|IllegalStateException|IllegalArgumentException expected){checks++;}
    }
    static <T>T edt(Callable<T> body)throws Exception {
        AtomicReference<T> result=new AtomicReference<>();AtomicReference<Throwable> failure=new AtomicReference<>();
        SwingUtilities.invokeAndWait(()->{try{result.set(body.call());}catch(Throwable error){failure.set(error);}});
        if(failure.get()!=null){if(failure.get() instanceof Exception)throw (Exception)failure.get();throw (Error)failure.get();}return result.get();
    }
    public static void main(String[] args)throws Exception {
        int exit=0;
        try{
            Path base=Files.createTempDirectory("gui-sensing79-fixture-").toRealPath();Path configDir=base.resolve("config"),manifest=base.resolve("prepared.json");
            Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,"success");
            yes(Files.getPosixFilePermissions(manifest).equals(java.nio.file.attribute.PosixFilePermissions.fromString("rw-------")),"prepared manifest is owner-only at creation");
            yes(Boolean.FALSE.equals(prepared.get("source_authority_created")),"preparer declares no source authority");
            yes(((NullDriver)Configuration.get().getMachine().getDrivers().get(0)).getControlledVacuumSource()==null,"preparer actually creates no source");
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());Configuration.get().load();
            Configuration config=Configuration.get();NullDriver driver=(NullDriver)config.getMachine().getDrivers().get(0);
            yes(driver.getControlledVacuumSource()==null,"native reload restores settings without source");
            String hash=(String)prepared.get("manifest_sha256");
            refused(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,hash,"success"));
            refused(()->edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,"0".repeat(64),"success")));
            refused(()->edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,hash,"invalid-read")));
            Path machine=configDir.resolve("machine.xml");byte[] original=Files.readAllBytes(machine);
            Files.writeString(machine,"\n",StandardOpenOption.APPEND);
            refused(()->edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,hash,"success")));
            Files.write(machine,original);Path extra=configDir.resolve("unexpected.xml");Files.writeString(extra,"<extra/>");
            refused(()->edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,hash,"success")));Files.delete(extra);
            yes(driver.getControlledVacuumSource()==null,"all denied claims left source absent");
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,hash,"success"));
            yes(Boolean.TRUE.equals(claimed.get("sensing_fixture_attested")),"exact prepared fixture claimed");
            yes(Configuration.get()==config,"GUI claim never replaces the active Configuration");
            yes(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"claim produces no enable or home");
            Object source=driver.getControlledVacuumSource();yes(source!=null,"fresh current-process source created");
            refused(()->edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,hash,"success")));
            yes(driver.getControlledVacuumSource()==source,"repeated claim cannot rotate source");
            String nozzle=config.getMachine().getDefaultHead().getDefaultNozzle().getId();
            Map<String,Object> reading=config.getMachine().submit(()->NativeVacuumSensing.admit(config,nozzle).measure(2,()->{},
                (event,n,data)->NativeVacuumSources.observe(config,event,n,data)),null,true).get(30,TimeUnit.SECONDS);
            yes(reading.get("samples").equals(List.of(70.0,70.0)),"actual native reads use declared synthetic source");
            refused(()->GuiSensingFixture.prepare(configDir,base.resolve("second.json"),"success"));
            yes(Files.readAllBytes(machine).length==original.length,"refused reprepare preserves existing machine settings");
            System.out.println("NATIVE_GUI_SENSING_FIXTURE_PASS "+checks+" checks; native settings and source claim only");
        }catch(Throwable failure){failure.printStackTrace();exit=1;}
        finally{try{if(Configuration.get()!=null&&Configuration.get().getMachine()!=null)Configuration.get().getMachine().close();}catch(Exception failure){failure.printStackTrace();exit=1;}}
        System.exit(exit);
    }
}
