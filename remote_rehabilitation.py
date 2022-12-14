from cvs import *
import math
import numpy as np
from scipy.special import expit
import time
import aidlite_gpu
import android
from upload_imgs import upload_images
from send_messages import send_mags

aidlite = aidlite_gpu.aidlite(1)

def resize_pad(img):
    """ resize and pad images to be input to the detectors
    The face and palm detector networks take 256x256 and 128x128 images
    as input. As such the input image is padded and resized to fit the
    size while maintaing the aspect ratio.
    Returns:
        img1: 256x256
        img2: 128x128
        scale: scale factor between original image and 256x256 image
        pad: pixels of padding in the original image
    """

    size0 = img.shape
    if size0[0]>=size0[1]:
        h1 = 256
        w1 = 256 * size0[1] // size0[0]
        padh = 0
        padw = 256 - w1
        scale = size0[1] / w1
    else:
        h1 = 256 * size0[0] // size0[1]
        w1 = 256
        padh = 256 - h1
        padw = 0
        scale = size0[0] / h1
    padh1 = padh//2
    padh2 = padh//2 + padh%2
    padw1 = padw//2
    padw2 = padw//2 + padw%2
    img1 = cv2.resize(img, (w1,h1))
    img1 = np.pad(img1, ((padh1, padh2), (padw1, padw2), (0,0)), 'constant', constant_values=(0,0))
    pad = (int(padh1 * scale), int(padw1 * scale))
    img2 = cv2.resize(img1, (128,128))
    return img1, img2, scale, pad
    
def denormalize_detections(detections, scale, pad):
    """ maps detection coordinates from [0,1] to image coordinates
    The face and palm detector networks take 256x256 and 128x128 images
    as input. As such the input image is padded and resized to fit the
    size while maintaing the aspect ratio. This function maps the
    normalized coordinates back to the original image coordinates.
    Inputs:
        detections: nxm tensor. n is the number of detections.
            m is 4+2*k where the first 4 valuse are the bounding
            box coordinates and k is the number of additional
            keypoints output by the detector.
        scale: scalar that was used to resize the image
        pad: padding in the x and y dimensions
    """
    detections[:, 0] = detections[:, 0] * scale * 256 - pad[0]
    detections[:, 1] = detections[:, 1] * scale * 256 - pad[1]
    detections[:, 2] = detections[:, 2] * scale * 256 - pad[0]
    detections[:, 3] = detections[:, 3] * scale * 256 - pad[1]

    detections[:, 4::2] = detections[:, 4::2] * scale * 256 - pad[1]
    detections[:, 5::2] = detections[:, 5::2] * scale * 256 - pad[0]
    return detections

def _decode_boxes(raw_boxes, anchors):
    """Converts the predictions into actual coordinates using
    the anchor boxes. Processes the entire batch at once.
    """
    boxes = np.zeros_like(raw_boxes)
    x_center = raw_boxes[..., 0] / 128.0 * anchors[:, 2] + anchors[:, 0]
    y_center = raw_boxes[..., 1] / 128.0 * anchors[:, 3] + anchors[:, 1]

    w = raw_boxes[..., 2] / 128.0 * anchors[:, 2]
    h = raw_boxes[..., 3] / 128.0 * anchors[:, 3]

    boxes[..., 0] = y_center - h / 2.  # ymin
    boxes[..., 1] = x_center - w / 2.  # xmin
    boxes[..., 2] = y_center + h / 2.  # ymax
    boxes[..., 3] = x_center + w / 2.  # xmax

    for k in range(4):
        offset = 4 + k*2
        keypoint_x = raw_boxes[..., offset    ] / 128.0 * anchors[:, 2] + anchors[:, 0]
        keypoint_y = raw_boxes[..., offset + 1] / 128.0 * anchors[:, 3] + anchors[:, 1]
        boxes[..., offset    ] = keypoint_x
        boxes[..., offset + 1] = keypoint_y

    return boxes

def _tensors_to_detections(raw_box_tensor, raw_score_tensor, anchors):
    """The output of the neural network is a tensor of shape (b, 896, 16)
    containing the bounding box regressor predictions, as well as a tensor 
    of shape (b, 896, 1) with the classification confidences.
    This function converts these two "raw" tensors into proper detections.
    Returns a list of (num_detections, 17) tensors, one for each image in
    the batch.
    This is based on the source code from:
    mediapipe/calculators/tflite/tflite_tensors_to_detections_calculator.cc
    mediapipe/calculators/tflite/tflite_tensors_to_detections_calculator.proto
    """
    detection_boxes = _decode_boxes(raw_box_tensor, anchors)
    
    thresh = 100.0
    raw_score_tensor = np.clip(raw_score_tensor, -thresh, thresh)
    detection_scores = expit(raw_score_tensor)
    
    # Note: we stripped off the last dimension from the scores tensor
    # because there is only has one class. Now we can simply use a mask
    # to filter out the boxes with too low confidence.
    mask = detection_scores >= 0.10

    # Because each image from the batch can have a different number of
    # detections, process them one at a time using a loop.
    boxes = detection_boxes[mask]
    scores = detection_scores[mask]
    scores = scores[..., np.newaxis]
    return np.hstack((boxes, scores))



def py_cpu_nms(dets, thresh):  
    """Pure Python NMS baseline."""  
    x1 = dets[:, 0]  
    y1 = dets[:, 1]  
    x2 = dets[:, 2]  
    y2 = dets[:, 3]  
    scores = dets[:, 12]  

    areas = (x2 - x1 + 1) * (y2 - y1 + 1)  
    #????????????????????????index  
    order = scores.argsort()[::-1]  
    #keep????????????????????????  
    keep = []  
    while order.size > 0:  
    #order[0]?????????????????????????????????????????????????????????????????????????????????  
        i = order[0]  
        keep.append(dets[i])  
        #????????????i????????????????????????????????????????????????????????????
        xx1 = np.maximum(x1[i], x1[order[1:]])  
        yy1 = np.maximum(y1[i], y1[order[1:]])  
        xx2 = np.minimum(x2[i], x2[order[1:]])  
        yy2 = np.minimum(y2[i], y2[order[1:]])  

        w = np.maximum(0.0, xx2 - xx1 + 1)  
        h = np.maximum(0.0, yy2 - yy1 + 1)  
        inter = w * h  
        #???/?????????iou???  
        ovr = inter / (areas[i] + areas[order[1:]] - inter)  
        #ind??????????????????i???iou?????????threshold???????????????index?????????????????????????????????i??????  
        inds = np.where(ovr <= thresh)[0]  
        #??????????????????????????????i???????????????i????????????order???????????????0?????????????????????1  
        order = order[inds + 1]  
    return keep
    
def denormalize_detections(detections, scale, pad):
    """ maps detection coordinates from [0,1] to image coordinates
    The face and palm detector networks take 256x256 and 128x128 images
    as input. As such the input image is padded and resized to fit the
    size while maintaing the aspect ratio. This function maps the
    normalized coordinates back to the original image coordinates.
    Inputs:
        detections: nxm tensor. n is the number of detections.
            m is 4+2*k where the first 4 valuse are the bounding
            box coordinates and k is the number of additional
            keypoints output by the detector.
        scale: scalar that was used to resize the image
        pad: padding in the x and y dimensions
    """
    detections[:, 0] = detections[:, 0] * scale * 256 - pad[0]
    detections[:, 1] = detections[:, 1] * scale * 256 - pad[1]
    detections[:, 2] = detections[:, 2] * scale * 256 - pad[0]
    detections[:, 3] = detections[:, 3] * scale * 256 - pad[1]

    detections[:, 4::2] = detections[:, 4::2] * scale * 256 - pad[1]
    detections[:, 5::2] = detections[:, 5::2] * scale * 256 - pad[0]
    return detections
    
def detection2roi(detection):
    """ Convert detections from detector to an oriented bounding box.
    Adapted from:
    # mediapipe/modules/face_landmark/face_detection_front_detection_to_roi.pbtxt
    The center and size of the box is calculated from the center 
    of the detected box. Rotation is calcualted from the vector
    between kp1 and kp2 relative to theta0. The box is scaled
    and shifted by dscale and dy.
    """
    kp1 = 2
    kp2 = 3
    theta0 = 90 * np.pi / 180
    dscale = 1.5
    dy = 0.
    xc = detection[:,4+2*kp1]
    yc = detection[:,4+2*kp1+1]
    x1 = detection[:,4+2*kp2]
    y1 = detection[:,4+2*kp2+1]
    scale = np.sqrt((xc-x1)**2 + (yc-y1)**2) * 2

    yc += dy * scale
    scale *= dscale

    # compute box rotation
    x0 = detection[:,4+2*kp1]
    y0 = detection[:,4+2*kp1+1]
    x1 = detection[:,4+2*kp2]
    y1 = detection[:,4+2*kp2+1]
    theta = np.arctan2(y0-y1, x0-x1) - theta0
    return xc, yc, scale, theta
    
def extract_roi(frame, xc, yc, theta, scale):

    # take points on unit square and transform them according to the roi
    points = np.array([[-1, -1, 1, 1],
                        [-1, 1, -1, 1]], dtype=np.float32).reshape(1,2,4)
    points = points * scale.reshape(-1,1,1)/2
    theta = theta.reshape(-1, 1, 1)
    R = np.concatenate((
        np.concatenate((np.cos(theta), -np.sin(theta)), 2),
        np.concatenate((np.sin(theta), np.cos(theta)), 2),
        ), 1)
    center = np.concatenate((xc.reshape(-1,1,1), yc.reshape(-1,1,1)), 1)
    points = R @ points + center

    # use the points to compute the affine transform that maps 
    # these points back to the output square
    res = 256
    points1 = np.array([[0, 0, res-1],
                        [0, res-1, 0]], dtype=np.float32).T
    affines = []
    imgs = []
    for i in range(points.shape[0]):
        pts = points[i, :, :3].T
        # print('pts', pts.shape, points1.shape, pts.dtype, points1.dtype)
        M = cv2.getAffineTransform(pts, points1)
        img = cv2.warpAffine(frame, M, (res,res))#, borderValue=127.5)
        imgs.append(img)
        affine = cv2.invertAffineTransform(M).astype('float32')
        affines.append(affine)
    if imgs:
        imgs = np.stack(imgs).astype(np.float32) / 255.#/ 127.5 - 1.0
        affines = np.stack(affines)
    else:
        imgs = np.zeros((0, 3, res, res))
        affines = np.zeros((0, 2, 3))

    return imgs, affines, points
    
def denormalize_landmarks(landmarks, affines):
    # landmarks[:,:,:2] *= 256
    for i in range(len(landmarks)):
        landmark, affine = landmarks[i], affines[i]
        landmark = (affine[:,:2] @ landmark[:,:2].T + affine[:,2:]).T
        landmarks[i,:,:2] = landmark
    return landmarks
    
def draw_detections(img, detections, with_keypoints=True):
    if detections.ndim == 1:
        detections = np.expand_dims(detections, axis=0)

    n_keypoints = detections.shape[1] // 2 - 2

    for i in range(detections.shape[0]):
        ymin = detections[i, 0]
        xmin = detections[i, 1]
        ymax = detections[i, 2]
        xmax = detections[i, 3]
        
        start_point = (int(xmin), int(ymin))
        end_point = (int(xmax), int(ymax))
        img = cv2.rectangle(img, start_point, end_point, (255, 0, 0), 1) 

        if with_keypoints:
            for k in range(n_keypoints):
                kp_x = int(detections[i, 4 + k*2    ])
                kp_y = int(detections[i, 4 + k*2 + 1])
                cv2.circle(img, (kp_x, kp_y), 2, (0, 0, 255), thickness=2)
    return img
    
def draw_roi(img, roi):
    for i in range(roi.shape[0]):
        (x1,x2,x3,x4), (y1,y2,y3,y4) = roi[i]
        cv2.line(img, (int(x1), int(y1)), (int(x2), int(y2)), (0,0,0), 2)
        cv2.line(img, (int(x1), int(y1)), (int(x3), int(y3)), (0,255,0), 2)
        cv2.line(img, (int(x2), int(y2)), (int(x4), int(y4)), (0,0,0), 2)
        cv2.line(img, (int(x3), int(y3)), (int(x4), int(y4)), (0,0,0), 2)
        
def draw_landmarks(img, points, connections=[], color=(255, 255, 0), size=2):
    for point in points:
        x, y = point
        x, y = int(x), int(y)
        cv2.circle(img, (x, y), size, color, thickness=size)
    for connection in connections:
        x0, y0 = points[connection[0]]
        x1, y1 = points[connection[1]]
        x0, y0 = int(x0), int(y0)
        x1, y1 = int(x1), int(y1)
        cv2.line(img, (x0, y0), (x1, y1), (255,255,255), size)


model_path = 'models/pose_detection.tflite'
model_pose = 'models/pose_landmark_upper_body.tflite'
# img_path = 'imgs/serena.png'

inShape =[1 * 128 * 128 *3*4,]
outShape = [1*896*12*4, 1*896*1*4]
print('gpu:',aidlite.ANNModel(model_path,inShape,outShape,4,0))
aidlite.set_g_index(1)
inShape =[1 * 256 * 256 *3*4,]
outShape = [1*155*4, 1*1*4, 1*128*128*1*4]
print('gpu:',aidlite.ANNModel(model_pose,inShape,outShape,4,0))

POSE_CONNECTIONS = [
    (0,1), (1,2), (2,3), (3,7),
    (0,4), (4,5), (5,6), (6,8),
    (9,10),
    (11,13), (13,15), (15,17), (17,19), (19,15), (15,21),
    (12,14), (14,16), (16,18), (18,20), (20,16), (16,22),
    (11,12), (12,24), (24,23), (23,11)
]
anchors = np.load('models/anchors.npy')
droid = android.Android()
droid.ttsSpeak('Sports change life')
# video_path = './videos/test1.mp4'
cap=cvs.VideoCapture(-1)

action_id = 1
raise_arms_count = 0
raised_flag = False
turn_round_count = 0
turn_left_flag = False
turn_right_flag = False
bend_arms_count = 0
bended_flag = False
totaltime = 0
stime = 0
# width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
# height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
# video_write = cv2.VideoWriter("video_save.mp4", cv2.VideoWriter_fourcc(*'MJPG'), 25,
#                                   (width, height), isColor=True)

while True:
    image = cvs.read()
    if image is None:
        continue
    image_roi=cv2.flip(image,1)
    
    frame = cv2.cvtColor(image_roi, cv2.COLOR_BGR2RGB)
    # frame = np.ascontiguousarray(frame[:,::-1,::-1])
    img1, img2, scale, pad = resize_pad(frame)
    img2 = img2.astype(np.float32)
    img2 = img2 / 255.# 127.5 - 1.0
    start_time = time.time()    
    aidlite.set_g_index(0)
    aidlite.setTensor_Fp32(img2,128,128)
    aidlite.invoke()
    bboxes  = aidlite.getTensor_Fp32(0).reshape(896, -1)
    scores  = aidlite.getTensor_Fp32(1)
    
    
    detections = _tensors_to_detections(bboxes, scores, anchors)
    normalized_pose_detections = py_cpu_nms(detections, 0.3)
    
    normalized_pose_detections  = np.stack(normalized_pose_detections ) if len(normalized_pose_detections ) > 0 else np.zeros((0, 12+1))
    pose_detections = denormalize_detections(normalized_pose_detections, scale, pad)
    if len(pose_detections) >0:
        xc, yc, scale, theta = detection2roi(pose_detections)
        img, affine, box = extract_roi(frame, xc, yc, theta, scale)
        
        # print(img.shape)
        
        aidlite.set_g_index(1)
        aidlite.setTensor_Fp32(img,256,256)
        aidlite.invoke()
        flags  = aidlite.getTensor_Fp32(1).reshape(-1,1)
        normalized_landmarks = aidlite.getTensor_Fp32(0).copy().reshape(1, 31, -1)
        mask = aidlite.getTensor_Fp32(2)
        
        
        landmarks = denormalize_landmarks(normalized_landmarks, affine)
        print('out', normalized_landmarks.shape, affine.shape, landmarks.shape, flags)
        
        # draw_roi(image_roi, box)
        t = (time.time() - start_time)
        # print('elapsed_ms invoke:',t*1000)
        lbs = 'Fps: '+ str(int(100/t)/100.)+" ~~ Time:"+str(t*1000) +"ms"
        cvs.setLbs(lbs) 
        for i in range(len(flags)):
            landmark, flag = landmarks[i], flags[i]
            if flag>.5:
                draw_landmarks(image_roi, landmark[:,:2], POSE_CONNECTIONS, size=2)
                
                points = landmark[:,:2]
                if action_id == 1:
                    if not turn_right_flag:
                        if points[0][0]-points[7][0] > 0:
                            turn_round_count += 1
                            turn_right_flag = True
                            droid.ttsSpeak('turn round %d times'%turn_round_count)
                    elif turn_right_flag:
                        if points[0][0]-points[7][0] < 0:
                            turn_right_flag = False
                        if turn_round_count == 6:
                            action_id = 2
                    if not turn_left_flag:
                        if points[8][0]-points[0][0] > 0:
                            turn_round_count += 1
                            turn_left_flag = True
                            droid.ttsSpeak('turn round %d times'%turn_round_count)
                    elif turn_left_flag:
                        if points[8][0]-points[0][0] < 0:
                            turn_left_flag = False
                        if turn_round_count == 6:
                            action_id = 2
                elif action_id == 2:
                    if not raised_flag:
                        if points[15][1] < points[11][1] and points[16][1] < points[12][1]:
                            raise_arms_count += 1
                            raised_flag = True
                            droid.ttsSpeak('raise hands %d times'%raise_arms_count)
                    elif raised_flag:
                        if points[15][1] > points[11][1] and points[16][1] > points[12][1]:
                            raised_flag = False
                        if raise_arms_count == 3:
                            action_id = 3
                elif action_id == 3:
                    if points[16][0] < points[14][0] and points[14][0] < points[12][0] and points[11][0] < points[13][0] and points[13][0] < points[15][0]:
                        k1 = (points[16][1]-points[14][1])/(float(points[16][0]- points[14][0]))
                        k2 = (points[14][1]-points[12][1])/(float(points[14][0]- points[12][0]))
                        x = np.array([1,k1])
                        y = np.array([1,k2])
                        Lx = np.sqrt(x.dot(x))
                        Ly = np.sqrt(y.dot(y))
                        cobb = int((np.arccos(x.dot(y)/(float(Lx*Ly)))*180/np.pi)+0.5)
                        # cv2.putText(image_roi, 'angle:%d'%cobb, (0, 60),cv2.FONT_ITALIC, 1, (0, 255, 129), 2)
                        if not bended_flag:
                            if cobb < 140 and cobb > 100:
                                if bend_arms_count == 3:
                                    bend_arms_count = 3
                                else:
                                    bend_arms_count += 1
                                    droid.ttsSpeak('bend arms %d times'%bend_arms_count)
                                bended_flag = True
                        elif bended_flag:
                            if cobb > 140 and cobb < 170:
                                bended_flag = False
    # if action_id == 1:
    cv2.putText(image_roi, 'turn round:%d'%turn_round_count, (0, 30),cv2.FONT_ITALIC, 1, (0, 255, 129), 2)
    # elif action_id == 2:
    cv2.putText(image_roi, 'raise hands:%d'%raise_arms_count, (0, 60),cv2.FONT_ITALIC, 1, (0, 255, 129), 2)
    # elif action_id == 3:
    cv2.putText(image_roi, 'bend arms:%d'%bend_arms_count, (0, 90),cv2.FONT_ITALIC, 1, (0, 255, 129), 2)
    if turn_round_count == 1:
        stime = time.time()
    elif bend_arms_count == 3:
        totaltime = time.time() - stime
        cv2.putText(image_roi, 'total time:%d s'%totaltime, (0, 120),cv2.FONT_ITALIC, 1, (255, 0, 0), 2)
        img_save_path = 'rehabilitation.jpeg'
        cv2.imwrite(img_save_path, image_roi)
        url_receive = upload_images(img_save_path, img_save_path)
        send_mags(url_receive)
        
    # video_write.write(image_roi)
    cvs.imshow(image_roi)

