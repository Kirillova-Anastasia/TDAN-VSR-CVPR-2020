import argparse
import sys
import scipy
import os
from PIL import Image
import torch
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
import numpy as np
from skimage import io, transform
from model import ModelFactory
from torch.autograd import Variable
import time
description='Video Super Resolution pytorch implementation'

def chop_forward(x, model, scale, shave=10, min_size=8000, n_GPUs=1):
    n_GPUs = min(n_GPUs, 4)
    b, num, c, h, w = x.size()
    h_half, w_half = h // 2, w // 2
    h_size, w_size = h_half + shave, w_half + shave
    inputlist = [
        x[:, :, :, 0:h_size, 0:w_size],
        x[:, :, :, 0:h_size, (w - w_size):w],
        x[:, :, :, (h - h_size):h, 0:w_size],
        x[:, :, :, (h - h_size):h, (w - w_size):w]]

    if w_size * h_size < min_size:
        outputlist = []
        for i in range(0, 4, n_GPUs):
            input_batch = torch.cat(inputlist[i:(i + n_GPUs)], dim=0)
            output_batch, _ = model(input_batch)
            outputlist.extend(output_batch.chunk(n_GPUs, dim=0))
    else:
        outputlist = [
            chop_forward(patch, model, scale, shave, min_size, n_GPUs) \
            for patch in inputlist]

    h, w = scale * h, scale * w
    h_half, w_half = scale * h_half, scale * w_half
    h_size, w_size = scale * h_size, scale * w_size
    shave *= scale

    output = Variable(x.data.new(b, c, h, w), volatile=True)
    output[:, :, 0:h_half, 0:w_half] \
        = outputlist[0][:, :, 0:h_half, 0:w_half]
    output[:, :, 0:h_half, w_half:w] \
        = outputlist[1][:, :, 0:h_half, (w_size - w + w_half):w_size]
    output[:, :, h_half:h, 0:w_half] \
        = outputlist[2][:, :, (h_size - h + h_half):h_size, 0:w_half]
    output[:, :, h_half:h, w_half:w] \
        = outputlist[3][:, :, (h_size - h + h_half):h_size, (w_size - w + w_half):w_size]

    return output


def forward_x8(lr, forward_function=None):
        def _transform(v, op):
            v = v.float()

            v2np = v.data.cpu().numpy()
            #print(v2np.shape)
            if op == 'v':
                tfnp = v2np[:, :, :, :, ::-1].copy()
            elif op == 'h':
                tfnp = v2np[:, :, :, ::-1, :].copy()
            elif op == 't':
                tfnp = v2np.transpose((0, 1, 2, 4, 3)).copy()
	
            ret = Variable(torch.Tensor(tfnp).cuda())
            #ret = ret.half()

            return ret

        def _transform_back(v, op):
       		
            if op == 'v':
                tfnp = v[:, :, :, ::-1].copy()
            elif op == 'h':
                tfnp = v[:, :, ::-1, :].copy()
            elif op == 't':
                tfnp = v.transpose((0, 1, 3, 2)).copy()
	
            return tfnp

        
        x = [lr]
        for tf in 'v', 'h': x.extend([_transform(_x, tf) for _x in x])
       
        list_r = []
        for k in range(len(x)):
            z = x[k]
            r, _ = forward_function(z)
            r = r.data.cpu().numpy()
            if k % 4 > 1:
                    r =  _transform_back(r, 'h')
            if (k % 4) % 2 == 1:
                    r =  _transform_back(r, 'v')
            list_r.append(r)
        y = np.sum(list_r,  axis=0)/4.0
       
        y = Variable(torch.Tensor(y).cuda())
        if len(y) == 1: y = y[0]
        return y
def quantize(img, rgb_range):
    return img.mul(255 / rgb_range).clamp(0, 255).round()


parser = argparse.ArgumentParser(description=description)

parser.add_argument('-m', '--model', metavar='M', type=str, default='TDAN',
                    help='network architecture.')
parser.add_argument('-s', '--scale', metavar='S', type=int, default=4, 
                    help='interpolation scale. Default 4')
parser.add_argument('-t', '--test-set', metavar='NAME', type=str, default='/home/cxu-serve/u1/ytian21/project/video_retoration/TDAN-VSR/data/Vid4',
                    help='dataset for testing.')
parser.add_argument('-mp', '--model-path', metavar='MP', type=str, default='model',
                    help='model path.')
parser.add_argument('-sp', '--save-path', metavar='SP', type=str, default='res',
                    help='saving directory path.')
args = parser.parse_args()

model_factory = ModelFactory()
model = model_factory.create_model(args.model)
dir_LR = args.test_set
lis = sorted(os.listdir(dir_LR))
model_path = os.path.join(args.model_path, 'model.pt')
if not os.path.exists(model_path):
    raise Exception('Cannot find %s.' %model_path)
model = torch.load(model_path)
model.eval()
path = args.save_path
if not os.path.exists(path):
            os.makedirs(path)

for i in range(len(lis)):
    if lis[i] == '.DS_Store':
        continue
    print(lis[i])
    LR = os.path.join(dir_LR, lis[i])#, 'LR_bicubic')
    ims = sorted(os.listdir(LR))
    num = len(ims)
    # number of the seq
    num = len(ims)
    image = io.imread(os.path.join(LR, ims[0]))
    row, col, ch = image.shape
    frames_lr = np.zeros((5, int(row), int(col), ch))
    for j in range(num):
        for k in range(j-2, j + 3):
            idx = k-j+2
            if k < 0:
                k = -k
            if k >= num:
                k = num - 3
            frames_lr[idx, :, :, :] = io.imread(os.path.join(LR, ims[k]))
        start = time.time()
        frames_lr = frames_lr/255.0 - 0.5
        lr = torch.from_numpy(frames_lr).float().permute(0, 3, 1, 2)
        lr = Variable(lr.cuda()).unsqueeze(0).contiguous()
        output = chop_forward(lr, model, args.scale)
        #output, _ = model(lr)
        #output = forward_x8(lr, model)
        output = (output.data + 0.5)*255
        output = quantize(output, 255)
        output = output.squeeze(dim=0)
        elapsed_time = time.time() - start
        #print(elapsed_time)
        img_name = os.path.join(os.path.join(path, lis[i]), ims[j])
        if not os.path.exists(os.path.join(path, lis[i])):
            os.makedirs(os.path.join(path, lis[i]))
        Image.fromarray(np.around(output.cpu().numpy().transpose(1, 2, 0)).astype(np.uint8)).save(img_name)
        
