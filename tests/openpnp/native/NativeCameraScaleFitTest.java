/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

/** Analytic boundary tests only; real detector/motion evidence is separate. */
public final class NativeCameraScaleFitTest {
    private static int checks;
    private interface Throwing {void run()throws Exception;}
    private static NativeCameraScaleFit.Point p(double x,double y)throws Exception{return new NativeCameraScaleFit.Point(x,y);}
    private static void near(double got,double expected){if(Math.abs(got-expected)>1e-10)throw new AssertionError(got+" != "+expected);checks++;}
    private static void rejects(String code,Throwing action)throws Exception{
        try{action.run();throw new AssertionError("Expected "+code);}catch(NativeCameraScaleFit.Rejected rejected){if(!rejected.code.equals(code))throw new AssertionError("Expected "+code+", got "+rejected.code);checks++;}
    }
    public static void main(String[] args)throws Exception {
        NativeCameraScaleFit.Point xm=p(332.5,240),xp=p(307.5,240),ym=p(320,227.5),yp=p(320,252.5);
        NativeCameraScaleFit.Fit fit=NativeCameraScaleFit.estimate(1,0.044,0.043,xm,xp,ym,yp);
        near(fit.unitsPerPixelX,0.04);near(fit.unitsPerPixelY,0.04);near(fit.determinant,-625);
        // Separate held-out coordinates; neither these nor baseline enter estimate().
        near(fit.check(p(320,240),0.3,-0.2,p(312.5,235)),0);
        near(fit.check(p(320,240),-0.2,0.3,p(325,247.5)),0);
        near(fit.check(p(320,240),0,0,p(321,240)),1);
        rejects("IMAGE_PREDICTION_ERROR",()->fit.check(p(320,240),0.3,-0.2,p(314,235)));
        rejects("CAMERA_ORIENTATION",()->NativeCameraScaleFit.estimate(1,0.044,0.043,xp,xm,ym,yp));
        rejects("CAMERA_ORIENTATION",()->NativeCameraScaleFit.estimate(1,0.044,0.043,xm,xp,yp,ym));
        rejects("CROSS_AXIS_RESPONSE",()->NativeCameraScaleFit.estimate(1,0.044,0.043,xm,p(307.5,240.3),ym,yp));
        rejects("IMAGE_DISPLACEMENT",()->NativeCameraScaleFit.estimate(1,0.044,0.043,xm,p(321,240),ym,yp));
        rejects("SCALE_CORRECTION",()->NativeCameraScaleFit.estimate(1,0.03,0.043,xm,xp,ym,yp));
        rejects("GEOMETRY_RANGE",()->NativeCameraScaleFit.estimate(1,Double.NaN,0.043,xm,xp,ym,yp));
        rejects("GEOMETRY_RANGE",()->NativeCameraScaleFit.estimate(1,0,0.043,xm,xp,ym,yp));
        rejects("DISPLACEMENT_RANGE",()->NativeCameraScaleFit.estimate(Double.POSITIVE_INFINITY,0.044,0.043,xm,xp,ym,yp));
        rejects("NONFINITE_DETECTION",()->p(Double.NaN,3));
        rejects("NONFINITE_POSITION",()->fit.predict(p(320,240),Double.NaN,0));
        // Legitimate distinct planar X/Y scales are supported; orientation/skew still gates them.
        NativeCameraScaleFit.Fit anisotropic=NativeCameraScaleFit.estimate(1,0.044,0.044,xm,xp,p(320,228),p(320,252));
        near(anisotropic.unitsPerPixelY,1.0/24);
        System.out.println("NativeCameraScaleFitTest PASS "+checks+" analytic assertions; no native image or hardware qualification");
    }
}
