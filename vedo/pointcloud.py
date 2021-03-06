from __future__ import division, print_function
import numpy as np
import vtk
import vedo
import vedo.colors as colors
import vedo.docs as docs
import vedo.settings as settings
import vedo.utils as utils
from vedo.base import BaseActor

from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from vtk.numpy_interface import dataset_adapter

__doc__ = ("""Submodule to manage point clouds."""
    + docs._defs
)

__all__ = ["Points",
           "Point",
           "cluster",
           "removeOutliers",
           "connectedPoints",
           "smoothMLS3D",
           "pointCloudFrom",
           "densifyCloud",
           "visiblePoints",
           "delaunay2D",
           "fitLine",
           "fitPlane",
           "fitSphere",
           "fitEllipsoid",
           "recoSurface",
           "pointSampler",
           ]


###################################################

def cluster(points, radius):
    """
    Clustering of points in space.

    `radius` is the radius of local search.
    Individual subsets can be accessed through ``mesh.clusters``.

    |clustering| |clustering.py|_
    """
    if isinstance(points, vtk.vtkActor):
        poly = points.GetMapper().GetInput()
    else:
        src = vtk.vtkPointSource()
        src.SetNumberOfPoints(len(points))
        src.Update()
        vpts = src.GetOutput().GetPoints()
        for i, p in enumerate(points):
            vpts.SetPoint(i, p)
        poly = src.GetOutput()

    cluster = vtk.vtkEuclideanClusterExtraction()
    cluster.SetInputData(poly)
    cluster.SetExtractionModeToAllClusters()
    cluster.SetRadius(radius)
    cluster.ColorClustersOn()
    cluster.Update()

    idsarr = cluster.GetOutput().GetPointData().GetArray("ClusterId")
    Nc = cluster.GetNumberOfExtractedClusters()

    sets = [[] for i in range(Nc)]
    for i, p in enumerate(points):
        sets[idsarr.GetValue(i)].append(p)

    acts = []
    for i, aset in enumerate(sets):
        acts.append(Points(aset, c=i))

    asse = vedo.assembly.Assembly(acts)

    asse.info["clusters"] = sets
    print("Nr. of extracted clusters", Nc)
    if Nc > 10:
        print("First ten:")
    for i in range(Nc):
        if i > 9:
            print("...")
            break
        print("Cluster #" + str(i) + ",  N =", len(sets[i]))
    print("Access individual clusters through attribute: obj.info['cluster']")
    return asse


def removeOutliers(points, radius, neighbors=5):
    """
    Remove outliers from a cloud of points within the specified `radius` search.

    |clustering| |clustering.py|_
    """
    isactor = False
    if isinstance(points, vtk.vtkActor):
        isactor = True
        poly = points.GetMapper().GetInput()
    else:
        src = vtk.vtkPointSource()
        src.SetNumberOfPoints(len(points))
        src.Update()
        vpts = src.GetOutput().GetPoints()
        for i, p in enumerate(points):
            vpts.SetPoint(i, p)
        poly = src.GetOutput()

    removal = vtk.vtkRadiusOutlierRemoval()
    removal.SetInputData(poly)
    removal.SetRadius(radius)
    removal.SetNumberOfNeighbors(neighbors)
    removal.GenerateOutliersOff()
    removal.Update()
    rpoly = removal.GetOutput()
    outpts = []
    for i in range(rpoly.GetNumberOfPoints()):
        outpts.append(list(rpoly.GetPoint(i)))
    outpts = np.array(outpts)
    if not isactor:
        return outpts

    return Points(outpts)



def smoothMLS3D(meshs, neighbours=10):
    """
    A time sequence of point clouds (Mesh) is being smoothed in 4D (3D + time)
    using a `MLS (Moving Least Squares)` algorithm variant.
    The time associated to an mesh must be specified in advance with ``mesh.time()`` method.
    Data itself can suggest a meaningful time separation based on the spatial
    distribution of points.

    :param int neighbours: fixed nr. of neighbours in space-time to take into account in the fit.

    |moving_least_squares3D| |moving_least_squares3D.py|_
    """
    from scipy.spatial import KDTree

    coords4d = []
    for a in meshs:  # build the list of 4d coordinates
        coords3d = a.points()
        n = len(coords3d)
        pttimes = [[a.time()]] * n
        coords4d += np.append(coords3d, pttimes, axis=1).tolist()

    avedt = float(meshs[-1].time() - meshs[0].time()) / len(meshs)
    print("Average time separation between meshes dt =", round(avedt, 3))

    coords4d = np.array(coords4d)
    newcoords4d = []
    kd = KDTree(coords4d, leafsize=neighbours)
    suggest = ""

    pb = utils.ProgressBar(0, len(coords4d))
    for i in pb.range():
        mypt = coords4d[i]

        # dr = np.sqrt(3*dx**2+dt**2)
        # iclosest = kd.query_ball_Point(mypt, r=dr)
        # dists, iclosest = kd.query(mypt, k=None, distance_upper_bound=dr)
        dists, iclosest = kd.query(mypt, k=neighbours)
        closest = coords4d[iclosest]

        nc = len(closest)
        if nc >= neighbours and nc > 5:
            m = np.linalg.lstsq(closest, [1.0] * nc)[0]  # needs python3
            vers = m / np.linalg.norm(m)
            hpcenter = np.mean(closest, axis=0)  # hyperplane center
            dist = np.dot(mypt - hpcenter, vers)
            projpt = mypt - dist * vers
            newcoords4d.append(projpt)

            if not i % 1000:  # work out some stats
                v = np.std(closest, axis=0)
                vx = round((v[0] + v[1] + v[2]) / 3, 3)
                suggest = "data suggest dt=" + str(vx)

        pb.print(suggest)
    newcoords4d = np.array(newcoords4d)

    ctimes = newcoords4d[:, 3]
    ccoords3d = np.delete(newcoords4d, 3, axis=1)  # get rid of time
    act = Points(ccoords3d)
    act.pointColors(ctimes, cmap="jet")  # use a colormap to associate a color to time
    return act



def connectedPoints(mesh, radius, mode=0, regions=(), vrange=(0,1), seeds=(), angle=0):
    """
    Extracts and/or segments points from a point cloud based on geometric distance measures
    (e.g., proximity, normal alignments, etc.) and optional measures such as scalar range.
    The default operation is to segment the points into "connected" regions where the connection
    is determined by an appropriate distance measure. Each region is given a region id.

    Optionally, the filter can output the largest connected region of points; a particular region
    (via id specification); those regions that are seeded using a list of input point ids;
    or the region of points closest to a specified position.

    The key parameter of this filter is the radius defining a sphere around each point which defines
    a local neighborhood: any other points in the local neighborhood are assumed connected to the point.
    Note that the radius is defined in absolute terms.

    Other parameters are used to further qualify what it means to be a neighboring point.
    For example, scalar range and/or point normals can be used to further constrain the neighborhood.
    Also the extraction mode defines how the filter operates.
    By default, all regions are extracted but it is possible to extract particular regions;
    the region closest to a seed point; seeded regions; or the largest region found while processing.
    By default, all regions are extracted.

    On output, all points are labeled with a region number.
    However note that the number of input and output points may not be the same:
    if not extracting all regions then the output size may be less than the input size.

    :param float radius: radius variable specifying a local sphere used to define local point neighborhood
    :param int mode:

        - 0,  Extract all regions
        - 1,  Extract point seeded regions
        - 2,  Extract largest region
        - 3,  Test specified regions
        - 4,  Extract all regions with scalar connectivity
        - 5,  Extract point seeded regions

    :param list regions: a list of non-negative regions id to extract
    :param list vrange: scalar range to use to extract points based on scalar connectivity
    :param list seeds: a list of non-negative point seed ids
    :param list angle: points are connected if the angle between their normals is
        within this angle threshold (expressed in degrees).
    """
    # https://vtk.org/doc/nightly/html/classvtkConnectedPointsFilter.html
    cpf = vtk.vtkConnectedPointsFilter()
    cpf.SetInputData(mesh.polydata())
    cpf.SetRadius(radius)
    if   mode == 0: # Extract all regions
        pass

    elif mode == 1: # Extract point seeded regions
        cpf.SetExtractionModeToPointSeededRegions()
        for s in seeds:
            cpf.AddSeed(s)

    elif mode == 2: # Test largest region
        cpf.SetExtractionModeToLargestRegion()

    elif mode == 3: # Test specified regions
        cpf.SetExtractionModeToSpecifiedRegions()
        for r in regions:
            cpf.AddSpecifiedRegion(r)

    elif mode == 4: # Extract all regions with scalar connectivity
        cpf.SetExtractionModeToLargestRegion()
        cpf.ScalarConnectivityOn()
        cpf.SetScalarRange(vrange[0], vrange[1])

    elif mode == 5: # Extract point seeded regions
        cpf.SetExtractionModeToLargestRegion()
        cpf.ScalarConnectivityOn()
        cpf.SetScalarRange(vrange[0], vrange[1])
        cpf.AlignedNormalsOn()
        cpf.SetNormalAngle(angle)

    cpf.Update()
    m = Points(cpf.GetOutput())
    m.name = "connectedPoints"
    return m


def pointCloudFrom(obj, interpolateCellData=False):
    """Build a `Mesh` object (as a point cloud) from any VTK dataset.

    :param bool interpolateCellData: if True cell data is interpolated at point positions.
    """
    if interpolateCellData:
        c2p = vtk.vtkCellDataToPointData()
        c2p.SetInputData(obj)
        c2p.Update()
        obj = c2p.GetOutput()

    wrapped = dataset_adapter.WrapDataObject(obj)
    ptdatanames = wrapped.PointData.keys()

    vpts = obj.GetPoints()
    poly = vtk.vtkPolyData()
    poly.SetPoints(vpts)

    for name in ptdatanames:
        arr = obj.GetPointData().GetArray(name)
        poly.GetPointData().AddArray(arr)

    m = Points(poly, c=None)
    m.name = "pointCloud"
    return m


def densifyCloud(mesh, targetDistance, closestN=6, radius=0, maxIter=None, maxN=None):
    """
    Add new points to an input point cloud.
    The new points are created in such a way that all points in any local neighborhood are
    within a target distance of one another.

    The algorithm works as follows. For each input point, the distance to all points
    in its neighborhood is computed. If any of its neighbors is further than the target distance,
    the edge connecting the point and its neighbor is bisected and a new point is inserted at the
    bisection point. A single pass is completed once all the input points are visited.
    Then the process repeats to the limit of the maximum number of iterations.

    .. note:: Points will be created in an iterative fashion until all points in their
        local neighborhood are the target distance apart or less.
        Note that the process may terminate early due to the limit on the
        maximum number of iterations. By default the target distance is set to 0.5.
        Note that the TargetDistance should be less than the Radius or nothing will change on output.

    .. warning:: This class can generate a lot of points very quickly.
        The maximum number of iterations is by default set to =1.0 for this reason.
        Increase the number of iterations very carefully.
        Also, `maxN` can be set to limit the explosion of points.
        It is also recommended that a N closest neighborhood is used.
    """
    src = vtk.vtkProgrammableSource()
    def readPoints():
        output = src.GetPolyDataOutput()
        points = vtk.vtkPoints()
        pts = mesh.points()
        for p in pts:
            x, y, z = p
            points.InsertNextPoint(x, y, z)
        output.SetPoints(points)
    src.SetExecuteMethod(readPoints)

    dens = vtk.vtkDensifyPointCloudFilter()
    dens.SetInputConnection(src.GetOutputPort())
    dens.InterpolateAttributeDataOn()
    dens.SetTargetDistance(targetDistance)
    if maxIter: dens.SetMaximumNumberOfIterations(maxIter)
    if maxN: dens.SetMaximumNumberOfPoints(maxN)

    if radius:
        dens.SetNeighborhoodTypeToRadius()
        dens.SetRadius(radius)
    elif closestN:
        dens.SetNeighborhoodTypeToNClosest()
        dens.SetNumberOfClosestPoints(closestN)
    else:
        colors.printc("Error in densifyCloud: set either radius or closestN", c=1)
        raise RuntimeError()
    dens.Update()
    pts = vtk_to_numpy(dens.GetOutput().GetPoints().GetData())
    cld = Points(pts, c=None).pointSize(3)
    cld.name = "densifiedCloud"
    return cld



def visiblePoints(mesh, area=(), tol=None, invert=False):
    """Extract points based on whether they are visible or not.
    Visibility is determined by accessing the z-buffer of a rendering window.
    The position of each input point is converted into display coordinates,
    and then the z-value at that point is obtained.
    If within the user-specified tolerance, the point is considered visible.
    Associated data attributes are passed to the output as well.

    This filter also allows you to specify a rectangular window in display (pixel)
    coordinates in which the visible points must lie.

    :param list area: specify a rectangular region as (xmin,xmax,ymin,ymax)
    :param float tol: a tolerance in normalized display coordinate system
    :param bool invert: select invisible points instead.

    :Example:
        .. code-block:: python

            from vedo import Ellipsoid, show, visiblePoints

            s = Ellipsoid().rotateY(30)

            #Camera options: pos, focalPoint, viewup, distance,
            # clippingRange, parallelScale, thickness, viewAngle
            camopts = dict(pos=(0,0,25), focalPoint=(0,0,0))
            show(s, camera=camopts, offscreen=True)

            m = visiblePoints(s)
            #print('visible pts:', m.points()) # numpy array
            show(m, newPlotter=True, axes=1)   # optionally draw result
    """
    # specify a rectangular region
    from vedo import settings
    svp = vtk.vtkSelectVisiblePoints()
    svp.SetInputData(mesh.polydata())
    svp.SetRenderer(settings.plotter_instance.renderer)

    if len(area)==4:
        svp.SetSelection(area[0],area[1],area[2],area[3])
    if tol is not None:
        svp.SetTolerance(tol)
    if invert:
        svp.SelectInvisibleOn()
    svp.Update()

    m = Points(svp.GetOutput()).pointSize(5)
    m.name = "VisiblePoints"
    return m



def delaunay2D(plist, mode='scipy', tol=None):
    """
    Create a mesh from points in the XY plane.
    If `mode='fit'` then the filter computes a best fitting
    plane and projects the points onto it.

    |delaunay2d| |delaunay2d.py|_
    """
    plist = np.ascontiguousarray(plist)

    if mode == 'scipy':
        try:
            from scipy.spatial import Delaunay as scipy_Delaunay
            tri = scipy_Delaunay(plist[:, 0:2])
            return vedo.mesh.Mesh([plist, tri.simplices])

        except:
            mode='xy'

    pd = vtk.vtkPolyData()
    vpts = vtk.vtkPoints()
    vpts.SetData(numpy_to_vtk(np.ascontiguousarray(plist), deep=True))
    pd.SetPoints(vpts)

    if plist.shape[1] == 2: # make it 3d
        plist = np.c_[plist, np.zeros(len(plist))]
    delny = vtk.vtkDelaunay2D()
    delny.SetInputData(pd)
    if tol:
        delny.SetTolerance(tol)

    if mode=='fit':
        delny.SetProjectionPlaneMode(vtk.VTK_BEST_FITTING_PLANE)
    delny.Update()
    return vedo.mesh.Mesh(delny.GetOutput())

def fitLine(points):
    """
    Fits a line through points.

    Extra info is stored in ``Line.slope``, ``Line.center``, ``Line.variances``.

    |fitline| |fitline.py|_
    """
    if isinstance(points, Points):
        points = points.points()
    data = np.array(points)
    datamean = data.mean(axis=0)
    uu, dd, vv = np.linalg.svd(data - datamean)
    vv = vv[0] / np.linalg.norm(vv[0])
    # vv contains the first principal component, i.e. the direction
    # vector of the best fit line in the least squares sense.
    xyz_min = points.min(axis=0)
    xyz_max = points.max(axis=0)
    a = np.linalg.norm(xyz_min - datamean)
    b = np.linalg.norm(xyz_max - datamean)
    p1 = datamean - a * vv
    p2 = datamean + b * vv
    l = vedo.shapes.Line(p1, p2, lw=1)
    l.slope = vv
    l.center = datamean
    l.variances = dd
    return l


def fitPlane(points):
    """
    Fits a plane to a set of points.

    Extra info is stored in ``Plane.normal``, ``Plane.center``, ``Plane.variance``.

    .. hint:: Example: |fitplanes.py|_
    """
    if isinstance(points, Points):
        points = points.points()
    data = np.array(points)
    datamean = data.mean(axis=0)
    res = np.linalg.svd(data - datamean)
    dd, vv = res[1], res[2]
    n = np.cross(vv[0], vv[1])
    xyz_min = points.min(axis=0)
    xyz_max = points.max(axis=0)
    s = np.linalg.norm(xyz_max - xyz_min)
    pla = vedo.shapes.Plane(datamean, n, s, s)
    pla.normal = n
    pla.center = datamean
    pla.variance = dd[2]
    pla.name = "fitPlane"
    return pla


def fitSphere(coords):
    """
    Fits a sphere to a set of points.

    Extra info is stored in ``Sphere.radius``, ``Sphere.center``, ``Sphere.residue``.

    .. hint:: Example: |fitspheres1.py|_

        |fitspheres2| |fitspheres2.py|_
    """
    if isinstance(coords, Points):
        coords = coords.points()
    coords = np.array(coords)
    n = len(coords)
    A = np.zeros((n, 4))
    A[:, :-1] = coords * 2
    A[:, 3] = 1
    f = np.zeros((n, 1))
    x = coords[:, 0]
    y = coords[:, 1]
    z = coords[:, 2]
    f[:, 0] = x * x + y * y + z * z
    C, residue, rank, sv = np.linalg.lstsq(A, f)  # solve AC=f
    if rank < 4:
        return None
    t = (C[0] * C[0]) + (C[1] * C[1]) + (C[2] * C[2]) + C[3]
    radius = np.sqrt(t)[0]
    center = np.array([C[0][0], C[1][0], C[2][0]])
    if len(residue):
        residue = np.sqrt(residue[0]) / n
    else:
        residue = 0
    s = vedo.shapes.Sphere(center, radius, c=(1,0,0)).wireframe(1)
    s.radius = radius # used by fitSphere
    s.center = center
    s.residue = residue
    s.name = "fitSphere"
    return s


def fitEllipsoid(points, pvalue=0.95):
    """
    Show the oriented PCA ellipsoid that contains fraction `pvalue` of points.

    :param float pvalue: ellypsoid will contain the specified fraction of points.

    Extra can be calculated with ``mesh.asphericity()``, ``mesh.asphericity_error()``
    (asphericity is equal to 0 for a perfect sphere).

    Axes can be accessed in ``mesh.va``, ``mesh.vb``, ``mesh.vc``.
    End point of the axes are stored in ``mesh.axis1``, ``mesh.axis12`` and ``mesh.axis3``.

    .. hint:: Examples: |pca.py|_  |cell_colony.py|_

         |pca| |cell_colony|
    """
    from scipy.stats import f

    if isinstance(points, Points):
        coords = points.points()
    else:
        coords = points
    if len(coords) < 4:
        colors.printc("Warning in fitEllipsoid(): not enough points!", c='y')
        return None

    P = np.array(coords, ndmin=2, dtype=float)
    cov = np.cov(P, rowvar=0)     # covariance matrix
    U, s, R = np.linalg.svd(cov)  # singular value decomposition
    p, n = s.size, P.shape[0]
    fppf = f.ppf(pvalue, p, n-p)*(n-1)*p*(n+1)/n/(n-p)  # f % point function
    cfac = 1 + 6/(n-1)            # correction factor for low statistics
    ua, ub, uc = np.sqrt(s*fppf)/cfac  # semi-axes (largest first)
    center = np.mean(P, axis=0)   # centroid of the hyperellipsoid

    elli = vedo.shapes.Ellipsoid((0,0,0), (1,0,0), (0,1,0), (0,0,1), alpha=0.2)

    matri = vtk.vtkMatrix4x4()
    matri.DeepCopy((R[0][0] * ua*2, R[1][0] * ub*2, R[2][0] * uc*2, center[0],
                    R[0][1] * ua*2, R[1][1] * ub*2, R[2][1] * uc*2, center[1],
                    R[0][2] * ua*2, R[1][2] * ub*2, R[2][2] * uc*2, center[2],
                    0, 0, 0, 1))
    vtra = vtk.vtkTransform()
    vtra.SetMatrix(matri)
    # assign the transformation
    elli.SetScale(vtra.GetScale())
    elli.SetOrientation(vtra.GetOrientation())
    elli.SetPosition(vtra.GetPosition())

    elli.GetProperty().BackfaceCullingOn()

    elli.nr_of_points = n
    elli.va = ua
    elli.vb = ub
    elli.vc = uc
    elli.axis1 = vtra.TransformPoint([1,0,0])
    elli.axis2 = vtra.TransformPoint([0,1,0])
    elli.axis3 = vtra.TransformPoint([0,0,1])
    elli.transformation = vtra
    elli.name = "fitEllipsoid"
    return elli


def recoSurface(pts, dims=(250,250,250), radius=None,
                sampleSize=None, holeFilling=True, bounds=(), pad=0.1):
    """
    Surface reconstruction from a scattered cloud of points.

    :param int dims: number of voxels in x, y and z to control precision.
    :param float radius: radius of influence of each point.
        Smaller values generally improve performance markedly.
        Note that after the signed distance function is computed,
        any voxel taking on the value >= radius
        is presumed to be "unseen" or uninitialized.
    :param int sampleSize: if normals are not present
        they will be calculated using this sample size per point.
    :param bool holeFilling: enables hole filling, this generates
        separating surfaces between the empty and unseen portions of the volume.
    :param list bounds: region in space in which to perform the sampling
        in format (xmin,xmax, ymin,ymax, zim, zmax)
    :param float pad: increase by this fraction the bounding box

    |recosurface| |recosurface.py|_
    """
    if not utils.isSequence(dims):
        dims = (dims,dims,dims)

    if isinstance(pts, Points):
        polyData = pts.polydata()
    else:
        polyData = vedo.pointcloud.Points(pts).polydata()

    sdf = vtk.vtkSignedDistance()

    if len(bounds)==6:
        sdf.SetBounds(bounds)
    else:
        x0, x1, y0, y1, z0, z1 = polyData.GetBounds()
        sdf.SetBounds(x0-(x1-x0)*pad, x1+(x1-x0)*pad,
                      y0-(y1-y0)*pad, y1+(y1-y0)*pad,
                      z0-(z1-z0)*pad, z1+(z1-z0)*pad)

    if polyData.GetPointData().GetNormals():
        sdf.SetInputData(polyData)
    else:
        normals = vtk.vtkPCANormalEstimation()
        normals.SetInputData(polyData)
        if not sampleSize:
            sampleSize = int(polyData.GetNumberOfPoints()/50)
        normals.SetSampleSize(sampleSize)
        normals.SetNormalOrientationToGraphTraversal()
        sdf.SetInputConnection(normals.GetOutputPort())
        #print("Recalculating normals with sample size =", sampleSize)

    if radius is None:
        b = polyData.GetBounds()
        diagsize = np.sqrt((b[1]-b[0])**2 + (b[3]-b[2])**2 + (b[5]-b[4])**2)
        radius = diagsize / (sum(dims)/3) * 5
        #print("Calculating mesh from points with radius =", radius)

    sdf.SetRadius(radius)
    sdf.SetDimensions(dims)
    sdf.Update()

    surface = vtk.vtkExtractSurface()
    surface.SetRadius(radius * 0.99)
    surface.SetHoleFilling(holeFilling)
    surface.ComputeNormalsOff()
    surface.ComputeGradientsOff()
    surface.SetInputConnection(sdf.GetOutputPort())
    surface.Update()
    return vedo.mesh.Mesh(surface.GetOutput())


def pointSampler(mesh, distance=None):
    """Generate a cloud of points the specified distance apart from the input."""
    poly = mesh.polydata()

    pointSampler = vtk.vtkPolyDataPointSampler()
    if not distance:
        distance = mesh.diagonalSize() / 100.0
    pointSampler.SetDistance(distance)
    #    pointSampler.GenerateVertexPointsOff()
    #    pointSampler.GenerateEdgePointsOff()
    #    pointSampler.GenerateVerticesOn()
    #    pointSampler.GenerateInteriorPointsOn()
    pointSampler.SetInputData(poly)
    pointSampler.Update()

    umesh = Points(pointSampler.GetOutput())
    prop = vtk.vtkProperty()
    prop.DeepCopy(mesh.GetProperty())
    umesh.SetProperty(prop)
    umesh.name = 'pointSampler'
    return umesh




###################################################
def Point(pos=(0, 0, 0), r=12, c="red", alpha=1):
    """Create a simple point."""
    if isinstance(pos, vtk.vtkActor):
        pos = pos.GetPosition()
    pd = utils.buildPolyData([[0,0,0]])
    if len(pos)==2:
        pos = (pos[0], pos[1], 0.)
    pt = Points(pd, c, alpha, r)
    pt.SetPosition(pos)
    pt.name = "Point"
    return pt

###################################################
class Points(vtk.vtkFollower, BaseActor):
    """
    Build a ``Mesh`` made of only vertex points for a list of 2D/3D points.
    Both shapes (N, 3) or (3, N) are accepted as input, if N>3.
    For very large point clouds a list of colors and alpha can be assigned to each
    point in the form `c=[(R,G,B,A), ... ]` where `0 <= R < 256, ... 0 <= A < 256`.
    :param float r: point radius.
    :param c: color name, number, or list of [R,G,B] colors of same length as plist.
    :type c: int, str, list
    :param float alpha: transparency in range [0,1].
    :param bool blur: make the point fluffy and blurred
        (works better with ``settings.useDepthPeeling=True``.)
    |manypoints.py|_ |lorenz.py|_
    |lorenz|
    """
    def __init__(
        self,
        inputobj=None,
        c=(0.2,0.2,0.2),
        alpha=1,
        r=4,
        blur=False,
    ):
        vtk.vtkActor.__init__(self)
        BaseActor.__init__(self)

        self._polydata = None
        self.point_locator = None
        self._mapper = vtk.vtkPolyDataMapper()
        self.SetMapper(self._mapper)

        prp = self.GetProperty()
        if settings.renderPointsAsSpheres:
            if hasattr(prp, 'RenderPointsAsSpheresOn'):
                prp.RenderPointsAsSpheresOn()


        if inputobj is None:
            self._polydata = vtk.vtkPolyData()
            return
        ##########

        prp.SetRepresentationToPoints()
        prp.SetPointSize(r)
        self.lighting(ambient=0.7, diffuse=0.3)
        # self.lighting('plastic')
        # prp.LightingOff()

        if isinstance(inputobj, vtk.vtkActor):
            polyCopy = vtk.vtkPolyData()
            polyCopy.DeepCopy(inputobj.GetMapper().GetInput())
            self._polydata = polyCopy
            self._mapper.SetInputData(polyCopy)
            self._mapper.SetScalarVisibility(inputobj.GetMapper().GetScalarVisibility())
            pr = vtk.vtkProperty()
            pr.DeepCopy(inputobj.GetProperty())
            self.SetProperty(pr)

        elif isinstance(inputobj, vtk.vtkPolyData):
            if inputobj.GetNumberOfCells() == 0:
                carr = vtk.vtkCellArray()
                for i in range(inputobj.GetNumberOfPoints()):
                    carr.InsertNextCell(1)
                    carr.InsertCellPoint(i)
                inputobj.SetVerts(carr)
            self._polydata = inputobj  # cache vtkPolyData and mapper for speed


        elif utils.isSequence(inputobj):
            plist = inputobj
            n = len(plist)

            if n == 3:  # assume plist is in the format [all_x, all_y, all_z]
                if utils.isSequence(plist[0]) and len(plist[0]) > 3:
                    plist = np.stack((plist[0], plist[1], plist[2]), axis=1)
            elif n == 2:  # assume plist is in the format [all_x, all_y, 0]
                if utils.isSequence(plist[0]) and len(plist[0]) > 3:
                    plist = np.stack((plist[0], plist[1], np.zeros(len(plist[0]))), axis=1)

            if n and len(plist[0]) == 2: # make it 3d
                plist = np.c_[np.array(plist), np.zeros(len(plist))]

            if ((utils.isSequence(c) and (len(c)>3 or (utils.isSequence(c[0]) and len(c[0])==4)))
                or utils.isSequence(alpha) ):

                cols = c

                n = len(plist)
                if n != len(cols):
                    colors.printc("Mismatch in Points() colors", n, len(cols), c=1)
                    raise RuntimeError()

                src = vtk.vtkPointSource()
                src.SetNumberOfPoints(n)
                src.Update()

                vgf = vtk.vtkVertexGlyphFilter()
                vgf.SetInputData(src.GetOutput())
                vgf.Update()
                pd = vgf.GetOutput()

                pd.GetPoints().SetData(numpy_to_vtk(np.ascontiguousarray(plist), deep=True))

                ucols = vtk.vtkUnsignedCharArray()
                ucols.SetNumberOfComponents(4)
                ucols.SetName("Points_RGBA")
                if utils.isSequence(alpha):
                    if len(alpha) != n:
                        colors.printc("Mismatch in Points() alphas", n, len(alpha), c=1)
                        raise RuntimeError()
                    alphas = alpha
                    alpha = 1
                else:
                   alphas = (alpha,) * n

                if utils.isSequence(cols):
                    c = None
                    if len(cols[0]) == 4:
                        for i in range(n): # FAST
                            rc,gc,bc,ac = cols[i]
                            ucols.InsertNextTuple4(rc, gc, bc, ac)
                    else:
                        for i in range(n): # SLOW
                            rc,gc,bc = colors.getColor(cols[i])
                            ucols.InsertNextTuple4(rc*255, gc*255, bc*255, alphas[i]*255)
                else:
                    c = cols

                pd.GetPointData().SetScalars(ucols)
                self._mapper.SetInputData(pd)
                self._mapper.ScalarVisibilityOn()

            else:

                pd = utils.buildPolyData(plist)
                self._mapper.SetInputData(pd)

                c = colors.getColor(c)
                prp.SetColor(c)
                prp.SetOpacity(alpha)

            if blur:
                point_mapper = vtk.vtkPointGaussianMapper()
                point_mapper.SetInputData(pd)
                point_mapper.SetScaleFactor(0.0005*self.diagonalSize()*r)
                point_mapper.EmissiveOn()
                self._mapper = point_mapper
                self.SetMapper(point_mapper)

            return
        ##########

        elif isinstance(inputobj, str):
            from vedo.io import load
            verts = load(inputobj).points()
            self._polydata = utils.buildPolyData(verts, None)

        else:
            colors.printc("Error: cannot build PointCloud from type:\n", [inputobj], c=1)
            raise RuntimeError()

        c = colors.getColor(c)
        prp.SetColor(c)
        prp.SetOpacity(alpha)

        self._mapper.SetInputData(self._polydata)


    ##################################################################################
    def _update(self, polydata):
        """Overwrite the polygonal mesh with a new vtkPolyData."""
        self._polydata = polydata
        self._mapper.SetInputData(polydata)
        self._mapper.Modified()
        return self

    def __add__(self, meshs):
        from vedo.assembly import Assembly
        if isinstance(meshs, list):
            alist = [self]
            for l in meshs:
                if isinstance(l, vtk.vtkAssembly):
                    alist += l.getMeshes()
                else:
                    alist += l
            return Assembly(alist)
        elif isinstance(meshs, vtk.vtkAssembly):
            meshs.AddPart(self)
            return meshs
        return Assembly([self, meshs])

    ##################################################################################
    # def polydata(self, transformed=True):
    #     """
    #     Returns the ``vtkPolyData`` object of a ``Mesh``.

    #     .. note:: If ``transformed=True`` returns a copy of polydata that corresponds
    #         to the current mesh's position in space.
    #     """
    #     if not transformed:
    #         if not self._polydata:
    #             self._polydata = self._mapper.GetInput()
    #         return self._polydata
    #     else:
    #         if self.GetIsIdentity() or self._polydata.GetNumberOfPoints()==0:
    #             # if identity return the original polydata
    #             if not self._polydata:
    #                 self._polydata = self._mapper.GetInput()
    #             return self._polydata
    #         else:
    #             # otherwise make a copy that corresponds to
    #             # the actual position in space of the mesh
    #             M = self.GetMatrix()
    #             transform = vtk.vtkTransform()
    #             transform.SetMatrix(M)
    #             tp = vtk.vtkTransformPolyDataFilter()
    #             tp.SetTransform(transform)
    #             tp.SetInputData(self._polydata)
    #             tp.Update()
    #             return tp.GetOutput()

    def polydata(self, transformed=True):
        """
        Returns the ``vtkPolyData`` object of a ``Mesh``.

        .. note:: If ``transformed=True`` returns a copy of polydata that corresponds
            to the current mesh's position in space.
        """
        if not self._polydata:
            self._polydata = self._mapper.GetInput()

        if transformed:
            if self.GetIsIdentity() or self._polydata.GetNumberOfPoints()==0:
                # no need to do much
                return self._polydata
            else:
                # otherwise make a copy that corresponds to
                # the actual position in space of the mesh
                M = self.GetMatrix()
                transform = vtk.vtkTransform()
                transform.SetMatrix(M)
                tp = vtk.vtkTransformPolyDataFilter()
                tp.SetTransform(transform)
                tp.SetInputData(self._polydata)
                tp.Update()
                return tp.GetOutput()
        else:
            return self._polydata


    def points(self, pts=None, transformed=True, copy=False):
        """
        Set/Get the vertex coordinates of the mesh.
        Argument can be an index, a set of indices
        or a complete new set of points to update the mesh.

        :param bool transformed: if `False` ignore any previous transformation
            applied to the mesh.
        :param bool copy: if `False` return the reference to the points
            so that they can be modified in place, otherwise a copy is built.
        """
        if pts is None: ### getter

            poly = self.polydata(transformed)
            vpts = poly.GetPoints()
            if vpts:
                if copy:
                    return np.array(vtk_to_numpy(vpts.GetData()))
                else:
                    return vtk_to_numpy(vpts.GetData())
            else:
                return np.array([])

        elif (utils.isSequence(pts) and not utils.isSequence(pts[0])) or isinstance(pts, (int, np.integer)):
            #passing a list of indices or a single index
            return vtk_to_numpy(self.polydata(transformed).GetPoints().GetData())[pts]

        else:           ### setter

            if len(pts) == 3 and len(pts[0]) != 3:
                # assume plist is in the format [all_x, all_y, all_z]
                pts = np.stack((pts[0], pts[1], pts[2]), axis=1)
            vpts = self._polydata.GetPoints()
            vpts.SetData(numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
            self._polydata.GetPoints().Modified()
            # reset mesh to identity matrix position/rotation:
            self.PokeMatrix(vtk.vtkMatrix4x4())
            return self


    def clone(self):
        """
        Clone a ``PointCloud`` object to make an exact copy of it.

        |mirror| |mirror.py|_
        """
        poly = self.polydata(False)
        polyCopy = vtk.vtkPolyData()
        polyCopy.DeepCopy(poly)

        cloned = Points(polyCopy)
        pr = vtk.vtkProperty()
        pr.DeepCopy(self.GetProperty())
        cloned.SetProperty(pr)

        if self.GetBackfaceProperty():
            bfpr = vtk.vtkProperty()
            bfpr.DeepCopy(self.GetBackfaceProperty())
            cloned.SetBackfaceProperty(bfpr)

        # assign the same transformation to the copy
        cloned.SetOrigin(self.GetOrigin())
        cloned.SetScale(self.GetScale())
        cloned.SetOrientation(self.GetOrientation())
        cloned.SetPosition(self.GetPosition())

        cloned._mapper.SetScalarVisibility(self._mapper.GetScalarVisibility())
        cloned._mapper.SetScalarRange(self._mapper.GetScalarRange())
        cloned._mapper.SetColorMode(self._mapper.GetColorMode())
        lsr = self._mapper.GetUseLookupTableScalarRange()
        cloned._mapper.SetUseLookupTableScalarRange(lsr)
        cloned._mapper.SetScalarMode(self._mapper.GetScalarMode())
        lut = self._mapper.GetLookupTable()
        if lut:
            cloned._mapper.SetLookupTable(lut)

        cloned.base = self.base
        cloned.top = self.top
        cloned.name = self.name
        if self.trail:
            n = len(self.trailPoints)
            cloned.addTrail(self.trailOffset, self.trailSegmentSize*n, n,
                            None, None, self.trail.GetProperty().GetLineWidth())
        if self.shadow:
            cloned.addShadow(self.shadowX, self.shadowY, self.shadowZ,
                             self.shadow.GetProperty().GetColor(),
                             self.shadow.GetProperty().GetOpacity())
        return cloned


    def clone2D(self, pos=(0,0), coordsys=4, scale=None,
                c=None, alpha=None, ps=2, lw=1,
                sendback=False, layer=0):
        """
        Copy a 3D Mesh into a static 2D image. Returns a ``vtkActor2D``.

            :param int coordsys: the coordinate system, options are

                0. Displays

                1. Normalized Display

                2. Viewport (origin is the bottom-left corner of the window)

                3. Normalized Viewport

                4. View (origin is the center of the window)

                5. World (anchor the 2d image to mesh)

            :param int ps: point size in pixel units
            :param int lw: line width in pixel units
            :param bool sendback: put it behind any other 3D object
        """
        msiz = self.diagonalSize()
        if scale is None:
            if settings.plotter_instance:
                sz = settings.plotter_instance.window.GetSize()
                dsiz = utils.mag(sz)
                scale = dsiz/msiz/9
            else:
                scale = 350/msiz
            colors.printc('clone2D(): scale set to', utils.precision(scale/300,3))
        else:
            scale *= 300

        cmsh = self.clone()

        if self.color() is not None or c is not None:
            cmsh._polydata.GetPointData().SetScalars(None)
            cmsh._polydata.GetCellData().SetScalars(None)
        poly = cmsh.pos(0,0,0).scale(scale).polydata()
        mapper2d = vtk.vtkPolyDataMapper2D()
        mapper2d.SetInputData(poly)
        act2d = vtk.vtkActor2D()
        act2d.SetMapper(mapper2d)
        act2d.SetLayerNumber(layer)
        csys = act2d.GetPositionCoordinate()
        csys.SetCoordinateSystem(coordsys)
        act2d.SetPosition(pos)
        if c is not None:
            c = colors.getColor(c)
            act2d.GetProperty().SetColor(c)
        else:
            act2d.GetProperty().SetColor(cmsh.color())
        if alpha is not None:
            act2d.GetProperty().SetOpacity(alpha)
        else:
            act2d.GetProperty().SetOpacity(cmsh.alpha())
        act2d.GetProperty().SetPointSize(ps)
        act2d.GetProperty().SetLineWidth(lw)
        act2d.GetProperty().SetDisplayLocationToForeground()
        if sendback:
            act2d.GetProperty().SetDisplayLocationToBackground()

        # print(csys.GetCoordinateSystemAsString())
        # print(act2d.GetHeight(), act2d.GetWidth(), act2d.GetLayerNumber())
        return act2d


    def addTrail(self, offset=None, maxlength=None, n=50, c=None, alpha=None, lw=2):
        """Add a trailing line to mesh.
        This new mesh is accessible through `mesh.trail`.

        :param offset: set an offset vector from the object center.
        :param maxlength: length of trailing line in absolute units
        :param n: number of segments to control precision
        :param lw: line width of the trail

        .. hint:: See examples: |trail.py|_  |airplanes.py|_

            |trail|
        """
        if maxlength is None:
            maxlength = self.diagonalSize() * 20
            if maxlength == 0:
                maxlength = 1

        if self.trail is None:
            pos = self.GetPosition()
            self.trailPoints = [None] * n
            self.trailSegmentSize = maxlength / n
            self.trailOffset = offset

            ppoints = vtk.vtkPoints()  # Generate the polyline
            poly = vtk.vtkPolyData()
            ppoints.SetData(numpy_to_vtk([pos] * n))
            poly.SetPoints(ppoints)
            lines = vtk.vtkCellArray()
            lines.InsertNextCell(n)
            for i in range(n):
                lines.InsertCellPoint(i)
            poly.SetPoints(ppoints)
            poly.SetLines(lines)
            mapper = vtk.vtkPolyDataMapper()

            if c is None:
                if hasattr(self, "GetProperty"):
                    col = self.GetProperty().GetColor()
                else:
                    col = (0.1, 0.1, 0.1)
            else:
                col = colors.getColor(c)

            if alpha is None:
                alpha = 1
                if hasattr(self, "GetProperty"):
                    alpha = self.GetProperty().GetOpacity()

            mapper.SetInputData(poly)
            tline = vedo.mesh.Mesh(poly, c=col, alpha=alpha)
            tline.SetMapper(mapper)
            tline.GetProperty().SetLineWidth(lw)
            self.trail = tline  # holds the vtkActor
        return self

    def updateTrail(self):
        currentpos = np.array(self.GetPosition())
        if self.trailOffset:
            currentpos += self.trailOffset
        lastpos = self.trailPoints[-1]
        if lastpos is None:  # reset list
            self.trailPoints = [currentpos] * len(self.trailPoints)
            return
        if np.linalg.norm(currentpos - lastpos) < self.trailSegmentSize:
            return

        self.trailPoints.append(currentpos)  # cycle
        self.trailPoints.pop(0)

        tpoly = self.trail.polydata()
        tpoly.GetPoints().SetData(numpy_to_vtk(self.trailPoints))
        return self


    def deletePoints(self, indices, renamePoints=False):
        """Delete a list of vertices identified by their index.

        :param bool renamePoints: if True, point indices and faces are renamed.
            If False, vertices are not really deleted and faces indices will
            stay unchanged (default, faster).

        |deleteMeshPoints| |deleteMeshPoints.py|_
        """
        cellIds = vtk.vtkIdList()
        self._polydata.BuildLinks()
        for i in indices:
            self._polydata.GetPointCells(i, cellIds)
            for j in range(cellIds.GetNumberOfIds()):
                self._polydata.DeleteCell(cellIds.GetId(j))  # flag cell

        self._polydata.RemoveDeletedCells()

        if renamePoints:
            coords = self.points(transformed=False)
            faces = self.faces()
            pts_inds = np.unique(faces) # flattened array

            newfaces = []
            for f in faces:
                newface=[]
                for i in f:
                    idx = np.where(pts_inds==i)[0][0]
                    newface.append(idx)
                newfaces.append(newface)

            newpoly = utils.buildPolyData(coords[pts_inds], newfaces)
            return self._update(newpoly)

        self._mapper.Modified()
        return self


    def computeNormalsWithPCA(self, n=20, orientationPoint=None, flip=False):
        """
        Generate point normals using PCA (principal component analysis).
        Basically this estimates a local tangent plane around each sample point p
        by considering a small neighborhood of points around p, and fitting a plane
        to the neighborhood (via PCA).

        :param int n: neighborhood size to calculate the normal
        :param list orientationPoint: adjust the +/- sign of the normals so that
            the normals all point towards a specified point. If None, perform a traversal
            of the point cloud and flip neighboring normals so that they are mutually consistent.

        :param bool flip: flip all normals
        """
        poly = self.polydata(False)
        pcan = vtk.vtkPCANormalEstimation()
        pcan.SetInputData(poly)
        pcan.SetSampleSize(n)

        if orientationPoint is not None:
            pcan.SetNormalOrientationToPoint()
            pcan.SetOrientationPoint(orientationPoint)
        else:
            pcan.SetNormalOrientationToGraphTraversal()

        if flip:
            pcan.FlipNormalsOn()

        pcan.Update()
        return self._update(pcan.GetOutput())


    def alpha(self, opacity=None):
        """Set/get mesh's transparency. Same as `mesh.opacity()`."""
        if opacity is None:
            return self.GetProperty().GetOpacity()

        self.GetProperty().SetOpacity(opacity)
        bfp = self.GetBackfaceProperty()
        if bfp:
            if opacity < 1:
                self._bfprop = bfp
                self.SetBackfaceProperty(None)
            else:
                self.SetBackfaceProperty(self._bfprop)
        return self

    def opacity(self, alpha=None):
        """Set/get mesh's transparency. Same as `mesh.alpha()`."""
        return self.alpha(alpha)


    def pointSize(self, ps=None):
        """Set/get mesh's point size of vertices. Same as `mesh.ps()`"""
        if ps is not None:
            if isinstance(self, vtk.vtkAssembly):
                cl = vtk.vtkPropCollection()
                self.GetActors(cl)
                cl.InitTraversal()
                a = vtk.vtkActor.SafeDownCast(cl.GetNextProp())
                a.GetProperty().SetRepresentationToPoints()
                a.GetProperty().SetPointSize(ps)
            else:
                self.GetProperty().SetRepresentationToPoints()
                self.GetProperty().SetPointSize(ps)
        else:
            return self.GetProperty().GetPointSize()
        return self

    def ps(self, pointSize=None):
        """Set/get mesh's point size of vertices. Same as `mesh.pointSize()`"""
        return self.pointSize(pointSize)

    def color(self, c=False):
        """
        Set/get mesh's color.
        If None is passed as input, will use colors from active scalars.
        Same as `mesh.c()`.
        """
        if c is False:
            return np.array(self.GetProperty().GetColor())
        elif c is None:
            self._mapper.ScalarVisibilityOn()
            return self
        self._mapper.ScalarVisibilityOff()
        cc = colors.getColor(c)
        self.GetProperty().SetColor(cc)
        if self.trail:
            self.trail.GetProperty().SetColor(cc)
        return self

    def clean(self, tol=None):
        """
        Clean mesh polydata. Can also be used to decimate a mesh if ``tol`` is large.
        If ``tol=None`` only removes coincident points.

        :param tol: defines how far should be the points from each other
            in terms of fraction of the bounding box length.

        |moving_least_squares1D| |moving_least_squares1D.py|_

            |recosurface| |recosurface.py|_
        """
        poly = self.polydata(False)
        cleanPolyData = vtk.vtkCleanPolyData()
        cleanPolyData.PointMergingOn()
        cleanPolyData.ConvertLinesToPointsOn()
        cleanPolyData.ConvertPolysToLinesOn()
        cleanPolyData.ConvertStripsToPolysOn()
        cleanPolyData.SetInputData(poly)
        if tol:
            cleanPolyData.SetTolerance(tol)
        cleanPolyData.Update()
        return self._update(cleanPolyData.GetOutput())


    def quantize(self, binSize):
        """
        The user should input binSize and all {x,y,z} coordinates
        will be quantized to that absolute grain size.

        Example:
            .. code-block:: python

                from vedo import Paraboloid
                Paraboloid().lw(0.1).quantize(0.1).show()
        """
        poly = self.polydata(False)
        qp = vtk.vtkQuantizePolyDataPoints()
        qp.SetInputData(poly)
        qp.SetQFactor(binSize)
        qp.Update()
        return self._update(qp.GetOutput())


    def averageSize(self):
        """Calculate the average size of a mesh.
        This is the mean of the vertex distances from the center of mass."""
        cm = self.centerOfMass()
        coords = self.points(copy=False)
        if not len(coords):
            return 0.0
        cc = coords-cm
        return np.mean(np.linalg.norm(cc, axis=1))

    def centerOfMass(self):
        """Get the center of mass of mesh.

        |fatlimb| |fatlimb.py|_
        """
        cmf = vtk.vtkCenterOfMass()
        cmf.SetInputData(self.polydata())
        cmf.Update()
        c = cmf.GetCenter()
        return np.array(c)


    def normalAt(self, i):
        """Return the normal vector at vertex point `i`."""
        normals = self.polydata().GetPointData().GetNormals()
        return np.array(normals.GetTuple(i))

    def normals(self, cells=False, compute=True):
        """Retrieve vertex normals as a numpy array.

        :params bool cells: if `True` return cell normals.
        :params bool compute: if `True` normals are recalculated if not already present.
            Note that this might modify the number of mesh points.
        """
        if cells:
            vtknormals = self.polydata().GetCellData().GetNormals()
        else:
            vtknormals = self.polydata().GetPointData().GetNormals()
        if not vtknormals and compute:
            self.computeNormals(cells=cells)
            if cells:
                vtknormals = self.polydata().GetCellData().GetNormals()
            else:
                vtknormals = self.polydata().GetPointData().GetNormals()
        if not vtknormals:
            return np.array([])
        return vtk_to_numpy(vtknormals)


    def labels(self, content=None, cells=False, scale=None,
               rotX=0, rotY=0, rotZ=0,
               ratio=1, precision=None, italic=False):
        """Generate value or ID labels for mesh cells or points.

        :param list,int,str content: either 'id', array name or array number.
            A array can also be passed (must match the nr. of points or cells).

        :param bool cells: generate labels for cells instead of points [False]
        :param float scale: absolute size of labels, if left as None it is automatic
        :param float rotX: local rotation angle of label in degrees
        :param int ratio: skipping ratio, to reduce nr of labels for large meshes
        :param int precision: numeric precision of labels

        :Example:
            .. code-block:: python

                from vedo import *
                s = Sphere(alpha=0.2, res=10).lineWidth(0.1)
                s.computeNormals().clean()
                point_ids = s.labels(cells=False).c('green')
                cell_ids  = s.labels(cells=True ).c('black')
                show(s, point_ids, cell_ids)

            |meshquality| |meshquality.py|_
        """
        if cells:
            elems = self.cellCenters()
            norms = self.normals(cells=True, compute=False)
            ns = np.sqrt(self.NCells())
        else:
            elems = self.points()
            norms = self.normals(cells=False, compute=False)
            ns = np.sqrt(self.NPoints())

        hasnorms=False
        if len(norms):
            hasnorms=True

        if scale is None:
            if not ns: ns = 100
            scale = self.diagonalSize()/ns/10

        arr = None
        mode = 0
        if content is None:
            mode=0
            if cells:
                name = self._polydata.GetCellData().GetScalars().GetName()
                arr = self.getCellArray(name)
            else:
                name = self._polydata.GetPointData().GetScalars().GetName()
                arr = self.getPointArray(name)
        elif isinstance(content, (str, int)):
            if content=='id':
                mode = 1
            elif cells:
                mode=0
                arr = self.getCellArray(content)
            else:
                mode=0
                arr = self.getPointArray(content)
        elif utils.isSequence(content):
            mode = 0
            arr = content
            if len(arr) != len(content):
                colors.printc('Error in labels(): array length mismatch',
                              len(arr), len(content), c=1)
                return None

        if arr is None and mode == 0:
            colors.printc('Error in labels(): array not found for points/cells', c=1)
            return None

        tapp = vtk.vtkAppendPolyData()
        for i,e in enumerate(elems):
            if i % ratio:
                continue
            tx = vtk.vtkVectorText()
            if mode==1:
                tx.SetText(str(i))
            else:
                if precision:
                    tx.SetText(utils.precision(arr[i], precision))
                else:
                    tx.SetText(str(arr[i]))
            tx.Update()

            T = vtk.vtkTransform()
            T.PostMultiply()
            if italic:
                T.Concatenate([1,0.25,0,0,
                               0,1,0,0,
                               0,0,1,0,
                               0,0,0,1])
            if hasnorms:
                ni = norms[i]
                if cells: # center-justify
                    bb = tx.GetOutput().GetBounds()
                    dx, dy = (bb[1]-bb[0])/2, (bb[3]-bb[2])/2
                    T.Translate(-dx,-dy,0)
                if rotX: T.RotateX(rotX)
                if rotY: T.RotateY(rotY)
                if rotZ: T.RotateZ(rotZ)
                crossvec = np.cross([0,0,1], ni)
                angle = np.arccos(np.dot([0,0,1], ni))*57.3
                T.RotateWXYZ(angle, crossvec)
                if cells: # small offset along normal only for cells
                    T.Translate(ni*scale/2)
            else:
                if rotX: T.RotateX(rotX)
                if rotY: T.RotateY(rotY)
                if rotZ: T.RotateZ(rotZ)
            T.Scale(scale,scale,scale)
            T.Translate(e)
            tf = vtk.vtkTransformPolyDataFilter()
            tf.SetInputData(tx.GetOutput())
            tf.SetTransform(T)
            tf.Update()
            tapp.AddInputData(tf.GetOutput())
        tapp.Update()
        ids = vedo.mesh.Mesh(tapp.GetOutput(), c=[.5,.5,.5])
        ids.lighting('off')
        return ids


    def alignTo(self, target, iters=100, rigid=False,
                invert=False, useCentroids=False):
        """
        Aligned to target mesh through the `Iterative Closest Point` algorithm.

        The core of the algorithm is to match each vertex in one surface with
        the closest surface point on the other, then apply the transformation
        that modify one surface to best match the other (in the least-square sense).

        :param bool rigid: if True do not allow scaling
        :param bool invert: if True start by aligning the target to the source but
             invert the transformation finally. Useful when the target is smaller
             than the source.

        :param bool useCentroids: start by matching the centroids of the two objects.

        .. hint:: |align1.py|_ |align2.py|_

             |align1| |align2|
        """
        icp = vtk.vtkIterativeClosestPointTransform()
        icp.SetSource(self.polydata())
        icp.SetTarget(target.polydata())
        if invert:
            icp.Inverse()
        icp.SetMaximumNumberOfIterations(iters)
        if rigid:
            icp.GetLandmarkTransform().SetModeToRigidBody()
        icp.SetStartByMatchingCentroids(useCentroids)
        icp.Update()

        if invert:
            T = icp.GetMatrix() # icp.GetInverse() doesnt work!
            T.Invert()
            self.applyTransform(T)
            self.transform = T
        else:
            self.applyTransform(icp)
            self.transform = icp

        return self


    def transformWithLandmarks(self, sourceLandmarks, targetLandmarks, rigid=False):
        """
        Trasform mesh orientation and position based on a set of landmarks points.
        The algorithm finds the best matching of source points to target points
        in the mean least square sense, in one single step.
        """
        lmt = vtk.vtkLandmarkTransform()

        if utils.isSequence(sourceLandmarks):
            ss = vtk.vtkPoints()
            for p in sourceLandmarks:
                ss.InsertNextPoint(p)
        else:
            ss = sourceLandmarks.polydata().GetPoints()

        if utils.isSequence(targetLandmarks):
            st = vtk.vtkPoints()
            for p in targetLandmarks:
                st.InsertNextPoint(p)
        else:
            st = targetLandmarks.polydata().GetPoints()

        if ss.GetNumberOfPoints() != st.GetNumberOfPoints():
            colors.printc('Error in transformWithLandmarks():', c=1)
            colors.printc('Source and Target have != nr of points',
                          ss.GetNumberOfPoints(), st.GetNumberOfPoints(), c=1)
            raise RuntimeError()

        lmt.SetSourceLandmarks(ss)
        lmt.SetTargetLandmarks(st)
        if rigid:
            lmt.SetModeToRigidBody()
        lmt.Update()
        self.applyTransform(lmt)
        self.transform = lmt
        return self


    def applyTransform(self, transformation):
        """
        Apply a linear or non-linear transformation to the mesh polygonal data.

        :param transformation: the``vtkTransform`` or ``vtkMatrix4x4`` objects.
        """
        if isinstance(transformation, vtk.vtkMatrix4x4):
            tr = vtk.vtkTransform()
            tr.SetMatrix(transformation)
            transformation = tr
        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetTransform(transformation)
        tf.SetInputData(self.polydata())
        tf.Update()
        self.PokeMatrix(vtk.vtkMatrix4x4())  # identity
        return self._update(tf.GetOutput())


    def normalize(self):
        """
        Scale Mesh average size to unit.
        """
        coords = self.points()
        if not len(coords):
            return self
        cm = np.mean(coords, axis=0)
        pts = coords - cm
        xyz2 = np.sum(pts * pts, axis=0)
        scale = 1 / np.sqrt(np.sum(xyz2) / len(pts))
        t = vtk.vtkTransform()
        t.Scale(scale, scale, scale)
        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetInputData(self._polydata)
        tf.SetTransform(t)
        tf.Update()
        return self._update(tf.GetOutput())


    def mirror(self, axis="x"):
        """
        Mirror the mesh  along one of the cartesian axes.

        |mirror| |mirror.py|_
        """
        sx, sy, sz = 1, 1, 1
        dx, dy, dz = self.GetPosition()
        if axis.lower() == "x":
            sx = -1
        elif axis.lower() == "y":
            sy = -1
        elif axis.lower() == "z":
            sz = -1
        elif axis.lower() == "n":
            pass
        else:
            colors.printc("Error in mirror(): mirror must be set to x, y, z or n.", c=1)
            raise RuntimeError()

        tr = vtk.vtkTransform()
        tr.Scale(sx,sy,sz)
        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetTransform(tr)
        tf.SetInputData(self._polydata)
        tf.Update()

        rs = vtk.vtkReverseSense()
        rs.SetInputData(tf.GetOutput())
        rs.ReverseNormalsOff()
        rs.Update()

        return self._update(rs.GetOutput())

    def shear(self, x=0, y=0, z=0):
        """
        Apply a shear deformation to the Mesh along one of the main axes.
        The transformation resets position and rotations so it should be applied first.
        """
        t = vtk.vtkTransform()
        sx, sy, sz = self.GetScale()
        t.SetMatrix([sx, x, 0, 0,
                      y,sy, z, 0,
                      0, 0,sz, 0,
                      0, 0, 0, 1])
        self.applyTransform(t)
        return self


    def flipNormals(self):
        """
        Flip all mesh normals. Same as `mesh.mirror('n')`.
        """
        rs = vtk.vtkReverseSense()
        rs.SetInputData(self._polydata)
        rs.ReverseCellsOff()
        rs.ReverseNormalsOn()
        rs.Update()
        return self._update(rs.GetOutput())


    def pointColors(self,
                    input_array=None,
                    cmap="jet",
                    alpha=1,
                    vmin=None, vmax=None,
                    arrayName="PointScalars",
                    ):
        """
        Set individual point colors by providing a list of scalar values and a color map.
        `scalars` can be a string name of the ``vtkArray``.

        :param list alphas: single value or list of transparencies for each vertex

        :param cmap: color map scheme to transform a real number into a color.
        :type cmap: str, list, vtkLookupTable, matplotlib.colors.LinearSegmentedColormap
        :param alpha: mesh transparency. Can be a ``list`` of values one for each vertex.
        :type alpha: float, list
        :param float vmin: clip scalars to this minimum value
        :param float vmax: clip scalars to this maximum value
        :param str arrayName: give a name to the array

        .. hint::|mesh_coloring.py|_ |mesh_alphas.py|_ |mesh_custom.py|_

             |mesh_coloring| |mesh_alphas| |mesh_custom|
        """
        poly = self.polydata(False)

        if input_array is None:             # if None try to fetch the active scalars
            arr = poly.GetPointData().GetScalars()
            if not arr:
                print('Cannot find any active point array ...skip coloring.')
                return self

        elif isinstance(input_array, str):  # if a name string is passed
            arr = poly.GetPointData().GetArray(input_array)
            if not arr:
                print('Cannot find point array with name:', input_array, '...skip coloring.')
                return self

        elif isinstance(input_array, int):  # if a int is passed
            if input_array < poly.GetPointData().GetNumberOfArrays():
                arr = poly.GetPointData().GetArray(input_array)
            else:
                print('Cannot find point array at position:', input_array, '...skip coloring.')
                return self

        elif utils.isSequence(input_array): # if a numpy array is passed
            n = len(input_array)
            if n != poly.GetNumberOfPoints():
                print('In pointColors(): nr. of scalars != nr. of points',
                      n, poly.GetNumberOfPoints(), '...skip coloring.')
                return self
            input_array = np.ascontiguousarray(input_array)
            arr = numpy_to_vtk(input_array, deep=True)
            arr.SetName(arrayName)

        elif isinstance(input_array, vtk.vtkArray): # if a vtkArray is passed
            arr = input_array

        else:
            print('In pointColors(): cannot understand input:', input_array)
            raise RuntimeError()

        ##########################
        arrfl = vtk.vtkFloatArray() #casting
        arrfl.ShallowCopy(arr)
        arr = arrfl

        if not arr.GetName():
            arr.SetName(arrayName)
        else:
            arrayName = arr.GetName()

        if not utils.isSequence(alpha):
            alpha = [alpha]*256

        if vmin is None:
            vmin = arr.GetRange()[0]
        if vmax is None:
            vmax = arr.GetRange()[1]

        ########################### build the look-up table
        lut = vtk.vtkLookupTable()
        lut.SetRange(vmin,vmax)
        if utils.isSequence(cmap):                 # manual sequence of colors
            ncols, nalpha = len(cmap), len(alpha)
            lut.SetNumberOfTableValues(ncols)
            for i, c in enumerate(cmap):
                r, g, b = colors.getColor(c)
                idx = int(i/ncols * nalpha)
                lut.SetTableValue(i, r, g, b, alpha[idx])
            lut.Build()

        elif isinstance(cmap, vtk.vtkLookupTable): # vtkLookupTable
            lut.DeepCopy(cmap)

        else: # assume string cmap name OR matplotlib.colors.LinearSegmentedColormap
            self.cmap = cmap
            ncols, nalpha = 256, len(alpha)
            lut.SetNumberOfTableValues(ncols)
            mycols = colors.colorMap(range(ncols), cmap, 0,ncols)
            for i,c in enumerate(mycols):
                r, g, b = c
                idx = int(i/ncols * nalpha)
                lut.SetTableValue(i, r, g, b, alpha[idx])
            lut.Build()

        self._mapper.SetLookupTable(lut)
        self._mapper.SetScalarModeToUsePointData()
        self._mapper.ScalarVisibilityOn()
        if hasattr(self._mapper, 'SetArrayName'):
            self._mapper.SetArrayName(arrayName)
        if settings.autoResetScalarRange:
            self._mapper.SetScalarRange(vmin, vmax)
        poly.GetPointData().SetScalars(arr)
        poly.GetPointData().SetActiveScalars(arrayName)
        poly.GetPointData().Modified()
        return self

    def pointGaussNoise(self, sigma):
        """
        Add gaussian noise to point positions.

        :param float sigma: sigma is expressed in percent of the diagonal size of mesh.

        :Example:
            .. code-block:: python

                from vedo import Sphere

                Sphere().addGaussNoise(1.0).show()
        """
        sz = self.diagonalSize()
        pts = self.points()
        n = len(pts)
        ns = np.random.randn(n, 3) * sigma * sz / 100
        vpts = vtk.vtkPoints()
        vpts.SetNumberOfPoints(n)
        vpts.SetData(numpy_to_vtk(pts + ns, deep=True))
        self._polydata.SetPoints(vpts)
        self._polydata.GetPoints().Modified()
        self.addPointArray(-ns, 'GaussNoise')
        return self


    def closestPoint(self, pt, N=1, radius=None, returnIds=False):
        """
        Find the closest point(s) on a mesh given from the input point `pt`.

        :param int N: if greater than 1, return a list of N ordered closest points.
        :param float radius: if given, get all points within that radius.
        :param bool returnIds: return points IDs instead of point coordinates.

        .. hint:: |align1.py|_ |fitplanes.py|_  |quadratic_morphing.py|_

            |align1| |quadratic_morphing|

        .. note:: The appropriate kd-tree search locator is built on the
            fly and cached for speed.
        """
        poly = self.polydata(True)

        if N > 1 or radius:
            plocexists = self.point_locator
            if not plocexists or (plocexists and self.point_locator is None):
                point_locator = vtk.vtkPointLocator()
                point_locator.SetDataSet(poly)
                point_locator.BuildLocator()
                self.point_locator = point_locator

            vtklist = vtk.vtkIdList()
            if N > 1:
                self.point_locator.FindClosestNPoints(N, pt, vtklist)
            else:
                self.point_locator.FindPointsWithinRadius(radius, pt, vtklist)
            if returnIds:
                return [int(vtklist.GetId(k)) for k in range(vtklist.GetNumberOfIds())]
            else:
                trgp = []
                for i in range(vtklist.GetNumberOfIds()):
                    trgp_ = [0, 0, 0]
                    vi = vtklist.GetId(i)
                    poly.GetPoints().GetPoint(vi, trgp_)
                    trgp.append(trgp_)
                return np.array(trgp)

        clocexists = self.cell_locator
        if not clocexists or (clocexists and self.cell_locator is None):
            cell_locator = vtk.vtkCellLocator()
            cell_locator.SetDataSet(poly)
            cell_locator.BuildLocator()
            self.cell_locator = cell_locator

        trgp = [0, 0, 0]
        cid = vtk.mutable(0)
        dist2 = vtk.mutable(0)
        subid = vtk.mutable(0)
        self.cell_locator.FindClosestPoint(pt, trgp, cid, subid, dist2)
        if returnIds:
            return int(cid)
        else:
            return np.array(trgp)


    def smoothMLS1D(self, f=0.2, radius=None):
        """
        Smooth mesh or points with a `Moving Least Squares` variant.
        The list ``mesh.info['variances']`` contain the residue calculated for each point.
        Input mesh's polydata is modified.

        :param float f: smoothing factor - typical range is [0,2].
        :param float radius: radius search in absolute units. If set then ``f`` is ignored.

        .. hint:: |moving_least_squares1D.py|_  |skeletonize.py|_

            |moving_least_squares1D| |skeletonize|
        """
        coords = self.points()
        ncoords = len(coords)

        if radius:
            Ncp=0
        else:
            Ncp = int(ncoords * f / 10)
            if Ncp < 5:
                colors.printc("Please choose a fraction higher than " + str(f), c=1)
                Ncp = 5

        variances, newline = [], []
        for i, p in enumerate(coords):

            points = self.closestPoint(p, N=Ncp, radius=radius)
            if len(points) < 4:
                continue

            points = np.array(points)
            pointsmean = points.mean(axis=0)  # plane center
            uu, dd, vv = np.linalg.svd(points - pointsmean)
            newp = np.dot(p - pointsmean, vv[0]) * vv[0] + pointsmean
            variances.append(dd[1] + dd[2])
            newline.append(newp)

        self.info["variances"] = np.array(variances)
        return self.points(newline)


    def smoothMLS2D(self, f=0.2, radius=None):
        """
        Smooth mesh or points with a `Moving Least Squares` algorithm variant.
        The list ``mesh.info['variances']`` contains the residue calculated for each point.

        :param float f: smoothing factor - typical range is [0,2].
        :param float radius: radius search in absolute units. If set then ``f`` is ignored.

        .. hint:: |moving_least_squares2D.py|_  |recosurface.py|_

            |moving_least_squares2D| |recosurface|
        """
        coords = self.points()
        ncoords = len(coords)

        if radius:
            Ncp = 0
        else:
            Ncp = int(ncoords * f / 100)
            if Ncp < 5:
                colors.printc("Please choose a fraction higher than " + str(f), c=1)
                Ncp = 5

        variances, newpts = [], []
        #pb = utils.ProgressBar(0, ncoords)
        for i, p in enumerate(coords):
            #pb.print("smoothing mesh ...")

            pts = self.closestPoint(p, N=Ncp, radius=radius)
            if radius and len(pts) < 5:
                continue

            ptsmean = pts.mean(axis=0)  # plane center
            _, dd, vv = np.linalg.svd(pts - ptsmean)
            cv = np.cross(vv[0], vv[1])
            t = (np.dot(cv, ptsmean) - np.dot(cv, p)) / np.dot(cv,cv)
            newp = p + cv*t
            newpts.append(newp)
            variances.append(dd[2])

        self.info["variances"] = np.array(variances)
        return self.points(newpts)



    def projectOnPlane(self, direction='z'):
        """
        Project the mesh on one of the Cartesian planes.
        """
        coords = self.points(transformed=1)
        if   'x' == direction:
            coords[:, 0] = self.GetOrigin()[0]
            self.x(self.xbounds()[0])
        elif 'y' == direction:
            coords[:, 1] = self.GetOrigin()[1]
            self.y(self.ybounds()[0])
        elif 'z' == direction:
            coords[:, 2] = self.GetOrigin()[2]
            self.z(self.zbounds()[0])
        else:
            colors.printc("Error in projectOnPlane(): unknown direction", direction, c=1)
            raise RuntimeError()
        self.alpha(0.1)
        self.points(coords)
        return self


    def warpToPoint(self, point, factor=0.1, absolute=True):
        """
        Modify the mesh coordinates by moving the vertices towards a specified point.

        :param float factor: value to scale displacement.
        :param list point: the position to warp towards.
        :param bool absolute: turning on causes scale factor of the new position
            to be one unit away from point.

        :Example:
            .. code-block:: python

                from vedo import *
                s = Cylinder(height=3).wireframe()
                pt = [4,0,0]
                w = s.clone().warpToPoint(pt, factor=0.5).wireframe(False)
                show(w,s, Point(pt), axes=1)

            |warpto|
        """
        warpTo = vtk.vtkWarpTo()
        warpTo.SetInputData(self._polydata)
        warpTo.SetPosition(point-self.pos())
        warpTo.SetScaleFactor(factor)
        warpTo.SetAbsolute(absolute)
        warpTo.Update()
        return self._update(warpTo.GetOutput())

    def warpByVectors(self, vects, factor=1, useCells=False):
        """Modify point coordinates by moving points along vector times the scale factor.
        Useful for showing flow profiles or mechanical deformation.
        Input can be an existing point/cell data array or a new array, in this case
        it will be named 'WarpVectors'.

        :parameter float factor: value to scale displacement
        :parameter bool useCell: if True, look for cell array instead of point array

        Example:
            .. code-block:: python

                from vedo import *
                b = load(datadir+'dodecahedron.vtk').computeNormals()
                b.warpByVectors("Normals", factor=0.15).show()

            |warpv|
        """
        wf = vtk.vtkWarpVector()
        wf.SetInputDataObject(self.polydata())

        if useCells:
            asso = vtk.vtkDataObject.FIELD_ASSOCIATION_CELLS
        else:
            asso = vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS

        vname = vects
        if utils.isSequence(vects):
            varr = numpy_to_vtk(np.ascontiguousarray(vects), deep=True)
            vname = "WarpVectors"
            if useCells:
                self.addCellArray(varr, vname)
            else:
                self.addPointArray(varr, vname)
        wf.SetInputArrayToProcess(0, 0, 0, asso, vname)
        wf.SetScaleFactor(factor)
        wf.Update()
        return self._update(wf.GetOutput())


    def thinPlateSpline(self, sourcePts, targetPts, userFunctions=(None,None), sigma=1):
        """
        `Thin Plate Spline` transformations describe a nonlinear warp transform defined by a set
        of source and target landmarks. Any point on the mesh close to a source landmark will
        be moved to a place close to the corresponding target landmark.
        The points in between are interpolated smoothly using Bookstein's Thin Plate Spline algorithm.

        Transformation object can be accessed with ``mesh.transform``.

        :param userFunctions: You may supply both the function and its derivative with respect to r.

        .. hint:: Examples: |thinplate_morphing1.py|_ |thinplate_morphing2.py|_ |thinplate_grid.py|_
            |thinplate_morphing_2d.py|_ |interpolateField.py|_

            |thinplate_morphing1| |thinplate_morphing2| |thinplate_grid| |interpolateField| |thinplate_morphing_2d|
        """
        if isinstance(sourcePts, Points):
            sourcePts = sourcePts.points()
        if isinstance(targetPts, Points):
            targetPts = targetPts.points()

        ns = len(sourcePts)
        ptsou = vtk.vtkPoints()
        ptsou.SetNumberOfPoints(ns)
        for i in range(ns):
            ptsou.SetPoint(i, sourcePts[i])

        nt = len(targetPts)
        if ns != nt:
            colors.printc("Error in thinPlateSpline(): #source != #target points", ns, nt, c=1)
            raise RuntimeError()

        pttar = vtk.vtkPoints()
        pttar.SetNumberOfPoints(nt)
        for i in range(ns):
            pttar.SetPoint(i, targetPts[i])

        transform = vtk.vtkThinPlateSplineTransform()
        transform.SetBasisToR()
        if userFunctions[0]:
            transform.SetBasisFunction(userFunctions[0])
            transform.SetBasisDerivative(userFunctions[1])
        transform.SetSigma(sigma)
        transform.SetSourceLandmarks(ptsou)
        transform.SetTargetLandmarks(pttar)
        self.transform = transform
        self.applyTransform(transform)
        return self


    def to_trimesh(self):
        """Return the trimesh object."""
        return utils.vedo2trimesh(self)













