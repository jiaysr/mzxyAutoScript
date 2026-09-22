import cv2
import numpy as np
import onnxruntime as ort

import ppocronnx.predict_system
from ppocronnx.cls import TextClassifier
from ppocronnx.det import TextDetector
from ppocronnx.rec import predict_rec
from ppocronnx.rec.predict_rec import TextRecognizer as _PpocrTextRecognizer
from ppocronnx.rec.rec_decoder import CTCLabelDecode
from ppocronnx.utility import get_character_dict, get_model_data, get_model_data_from_path


class TextRecognizer(_PpocrTextRecognizer):
    """
    支持更换识别模型的 TextRecognizer

    相比 ppocronnx 自带的实现：
        1. rec_image_shape 可配置，PP-OCRv4/v5 识别模型的输入高度是 48，而不是 v2 的 32
        2. 支持更换字典（PP-OCRv5 的字典和 ppocr_keys_v1 不同）
        3. 修正宽度计算，按输入高度计算而不是写死 32，长文本不会被横向压缩
    """

    def __init__(self, rec_model_path=None, ort_providers=None, rec_image_shape=(3, 32, 320),
                 char_dict=None, max_rec_width=960, rec_batch_num=6):
        if ort_providers is None:
            ort_providers = ['CPUExecutionProvider']
        self.rec_image_shape = list(rec_image_shape)
        self.max_rec_width = max_rec_width
        self.character_type = 'ch'
        self.rec_batch_num = rec_batch_num
        self.rec_algorithm = 'CRNN'
        if char_dict is None:
            char_dict = get_character_dict()
        self.postprocess_op = CTCLabelDecode(character_dict=char_dict, character_type='ch', use_space_char=True)
        if rec_model_path is None:
            model_data = get_model_data(predict_rec.rec_model_file)
        else:
            model_data = get_model_data_from_path(rec_model_path)
        so = ort.SessionOptions()
        so.log_severity_level = 3
        sess = ort.InferenceSession(model_data, so, providers=ort_providers)
        self.predictor, self.input_tensor = sess, sess.get_inputs()[0]
        self.output_tensors = None

    def resize_norm_img(self, img, max_wh_ratio):
        imgC, imgH, imgW = self.rec_image_shape
        assert imgC == img.shape[2]
        imgW = max(min(int(imgH * max_wh_ratio), self.max_rec_width), 32)
        h, w = img.shape[:2]
        ratio = w / float(h)
        resized_w = min(int(np.ceil(imgH * ratio)), imgW)
        resized_image = cv2.resize(img, (resized_w, imgH))
        resized_image = resized_image.astype('float32')
        resized_image = resized_image.transpose((2, 0, 1)) / 255
        resized_image -= 0.5
        resized_image /= 0.5
        padding_im = np.zeros((imgC, imgH, imgW), dtype=np.float32)
        padding_im[:, :, 0:resized_w] = resized_image
        return padding_im


class TextSystem(ppocronnx.predict_system.TextSystem):
    """
    与 ppocronnx 的 TextSystem 接口一致，但是检测和识别模型都可以替换
    """

    def __init__(
            self,
            use_angle_cls=False,
            box_thresh=0.6,
            unclip_ratio=1.6,
            rec_model_path=None,
            det_model_path=None,
            ort_providers=None,
            rec_image_shape=None,
            char_dict_path=None,
            max_rec_width=960,
    ):
        self.text_detector = TextDetector(box_thresh=box_thresh, unclip_ratio=unclip_ratio,
                                          det_model_path=det_model_path, ort_providers=ort_providers)
        char_dict = None
        if char_dict_path:
            with open(char_dict_path, encoding='utf-8') as f:
                char_dict = f.read().splitlines()
        self.text_recognizer = TextRecognizer(
            rec_model_path=rec_model_path,
            ort_providers=ort_providers,
            rec_image_shape=rec_image_shape or (3, 32, 320),
            char_dict=char_dict,
            max_rec_width=max_rec_width,
        )
        self.use_angle_cls = use_angle_cls
        if self.use_angle_cls:
            self.text_classifier = TextClassifier(ort_providers=ort_providers)


def sorted_boxes(dt_boxes):
    """
    Sort text boxes in order from top to bottom, left to right
    args:
        dt_boxes(array):detected text boxes with shape [4, 2]
    return:
        sorted boxes(array) with shape [4, 2]
    """
    num_boxes = dt_boxes.shape[0]
    sorted_boxes = sorted(dt_boxes, key=lambda x: (x[0][1], x[0][0]))
    _boxes = list(sorted_boxes)

    for i in range(num_boxes - 1):
        for j in range(i, -1, -1):
            if abs(_boxes[j + 1][0][1] - _boxes[j][0][1]) < 10 and \
                    (_boxes[j + 1][0][0] < _boxes[j][0][0]):
                tmp = _boxes[j]
                _boxes[j] = _boxes[j + 1]
                _boxes[j + 1] = tmp
            else:
                break
    return _boxes

# sorted_boxes() from PaddleOCR 2.6, newer and better than the one in ppocr-onnx
ppocronnx.predict_system.sorted_boxes = sorted_boxes
