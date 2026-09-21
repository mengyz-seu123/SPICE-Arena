"""Axis-aligned largest squares inside piecewise-linear butterfly lobes.

For decreasing lower/upper boundaries L,U, a square of side a fits at x iff
U(x+a)-L(x)>=a. Checking both curves' breakpoints is exact for linear segments.
This is NOT the largest vertical/diagonal separation between the curves.
"""
from bisect import bisect_left,bisect_right
from .common import at

def _decreasing(points):
    xs,ys=map(list,zip(*points))
    if any(b<=a for a,b in zip(xs,xs[1:])):raise ValueError('SNM curve x must strictly increase')
    if any(b>a+1e-7 for a,b in zip(ys,ys[1:])):raise ValueError('nonmonotonic inverter transfer, SNM unsupported')
    return xs,ys

def largest_square(lower,upper,lo,hi):
    lx,ly=lower;ux,uy=upper
    def fits(a):
        end=hi-a
        if end<lo:return False
        positions=[lo,end]
        positions+=lx[bisect_right(lx,lo):bisect_left(lx,end)]
        positions += [v-a for v in ux[bisect_right(ux,lo+a):bisect_left(ux,hi)]]
        return any(at(ux,uy,x+a)-at(lx,ly,x)>=a-1e-12 for x in positions)
    low,high=0.,hi-lo
    for _ in range(45):
        mid=(low+high)/2
        if fits(mid):low=mid
        else:high=mid
    return low

def butterfly(x,fa,fb):
    # Original equations Q=fa(QB), QB=fb(Q); both curves share axes (Q,QB).
    bx,by=_decreasing(list(zip(x,fb)))
    pairs=[]
    for q,qb in reversed(list(zip(fa,x))):
        if pairs and abs(q-pairs[-1][0])<1e-13:continue
        pairs.append((q,qb))
    ax,ay=_decreasing(pairs)
    lo=max(ax[0],bx[0]);hi=min(ax[-1],bx[-1])
    knots=sorted({lo,hi,*[v for v in ax if lo<v<hi],*[v for v in bx if lo<v<hi]})
    diffs=[at(ax,ay,z)-at(bx,by,z) for z in knots]
    roots=[]
    for i,(z,d) in enumerate(zip(knots,diffs)):
        if abs(d)<1e-12:roots.append(z)
        if i and d*diffs[i-1]<0:roots.append(knots[i-1]+(z-knots[i-1])*(-diffs[i-1])/(d-diffs[i-1]))
    roots=sorted(roots);unique=[]
    for z in roots:
        if not unique or z-unique[-1]>1e-8:unique.append(z)
    if len(unique)!=3:
        return {'read_snm_mv':0.,'lobes_mv':[0.,0.],'intersections_v':unique,'bistable':False,'reason':'expected three butterfly intersections'}
    lobes=[]
    for a,b in zip(unique,unique[1:]):
        mid=(a+b)/2
        if at(ax,ay,mid)>at(bx,by,mid):lower,upper=(bx,by),(ax,ay)
        else:lower,upper=(ax,ay),(bx,by)
        lobes.append(1000*largest_square(lower,upper,a,b))
    return {'read_snm_mv':min(lobes),'lobes_mv':lobes,'intersections_v':unique,'bistable':min(lobes)>0,'algorithm':'largest axis-aligned square in piecewise-linear lobe'}
