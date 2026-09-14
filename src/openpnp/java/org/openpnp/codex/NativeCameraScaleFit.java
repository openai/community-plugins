/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

/** Pure interpretation of separately acquired native image observations.
 * This class neither reads a camera model nor changes calibration. Commanded
 * simulator displacement is its distance reference, not physical metrology. */
final class NativeCameraScaleFit {
    static final double MIN_PAIRED_DISPLACEMENT_PX=12;
    static final double MAX_SCALE_CHANGE=0.20;
    static final double MAX_CROSS_AXIS_RATIO=0.01;
    static final double MAX_PREDICTION_ERROR_PX=1;

    static final class Rejected extends Exception {
        final String code;
        Rejected(String code,String message){super(message);this.code=code;}
    }
    static final class Point {
        final double x,y;
        Point(double x,double y)throws Rejected {
            if(!Double.isFinite(x)||!Double.isFinite(y))throw new Rejected("NONFINITE_DETECTION","Detection center must be finite");
            this.x=x;this.y=y;
        }
    }
    static final class Fit {
        final double unitsPerPixelX,unitsPerPixelY,xx,xy,yx,yy,determinant;
        Fit(double ux,double uy,double xx,double xy,double yx,double yy){this.unitsPerPixelX=ux;this.unitsPerPixelY=uy;this.xx=xx;this.xy=xy;this.yx=yx;this.yy=yy;this.determinant=xx*yy-xy*yx;}
        Point predict(Point baseline,double dxMm,double dyMm)throws Rejected {
            if(!Double.isFinite(dxMm)||!Double.isFinite(dyMm))throw new Rejected("NONFINITE_POSITION","Prediction displacement must be finite");
            return new Point(baseline.x-dxMm/unitsPerPixelX,baseline.y+dyMm/unitsPerPixelY);
        }
        double check(Point baseline,double dxMm,double dyMm,Point observed)throws Rejected {
            Point predicted=predict(baseline,dxMm,dyMm);
            double error=Math.hypot(observed.x-predicted.x,observed.y-predicted.y);
            if(!Double.isFinite(error)||error>MAX_PREDICTION_ERROR_PX)throw new Rejected("IMAGE_PREDICTION_ERROR","Held-out or final-baseline image prediction error exceeds one pixel");
            return error;
        }
    }
    private NativeCameraScaleFit() { }

    /** Four training images only. Baseline/holdout images cannot enter this fit. */
    static Fit estimate(double displacementMm,double approximateX,double approximateY,
                        Point xMinus,Point xPlus,Point yMinus,Point yPlus)throws Rejected {
        if(!Double.isFinite(displacementMm)||displacementMm<0.5||displacementMm>2)
            throw new Rejected("DISPLACEMENT_RANGE","Displacement must be within 0.5..2 millimeters");
        if(!Double.isFinite(approximateX)||!Double.isFinite(approximateY)||approximateX<=0||approximateY<=0)
            throw new Rejected("GEOMETRY_RANGE","Existing approximate planar scale must be finite and positive");
        double xx=xPlus.x-xMinus.x,xy=xPlus.y-xMinus.y,yx=yPlus.x-yMinus.x,yy=yPlus.y-yMinus.y;
        // Head-mounted down-looking camera: +X shifts a target left, +Y down.
        // Taking absolute values alone would silently accept reversed axes.
        if(xx>=0||yy<=0)throw new Rejected("CAMERA_ORIENTATION","Observed image motion has an unsupported sign or orientation");
        if(Math.abs(xx)<MIN_PAIRED_DISPLACEMENT_PX||Math.abs(yy)<MIN_PAIRED_DISPLACEMENT_PX)
            throw new Rejected("IMAGE_DISPLACEMENT","Paired image displacement is below twelve pixels");
        if(Math.abs(xy)/Math.abs(xx)>MAX_CROSS_AXIS_RATIO||Math.abs(yx)/Math.abs(yy)>MAX_CROSS_AXIS_RATIO)
            throw new Rejected("CROSS_AXIS_RESPONSE","Observed cross-axis image response exceeds one percent");
        double ux=-displacementMm/xx,uy=displacementMm/yy,det=xx*yy-xy*yx;
        if(!Double.isFinite(ux)||!Double.isFinite(uy)||ux<=0||uy<=0||!Double.isFinite(det)||det>=0)
            throw new Rejected("DEGENERATE_FIT","Image displacement matrix is not an admitted planar fit");
        if(Math.abs(ux/approximateX-1)>MAX_SCALE_CHANGE||Math.abs(uy/approximateY-1)>MAX_SCALE_CHANGE)
            throw new Rejected("SCALE_CORRECTION","Measured scale differs by more than twenty percent from approximate geometry");
        return new Fit(ux,uy,xx,xy,yx,yy);
    }
}
