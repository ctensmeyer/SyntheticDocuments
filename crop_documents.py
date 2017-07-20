#!/usr/bin/env python3
import os
import random
import argparse
import numpy as np
import errno
import shutil
import lmdb
import caffe.proto.caffe_pb2
import traceback
import sys
import io
import matplotlib.pyplot as plt

import cv2
from multiprocessing import Pool

GRAYSCALE = True

ORIGINAL_DIR = "/data/synthetic_trial_results/"
RESULTS_DIR = "/data/synthetic_trial_final/"
GARBAGE_DIR = "/data/garbage/"

FULL_DIR = RESULTS_DIR + "full/"

TRAIN_DIR = RESULTS_DIR + "train/"
VAL_DIR = RESULTS_DIR + "val/"
TEST_DIR = RESULTS_DIR + "test/"

LABELS_DIR = RESULTS_DIR + "labels/"

LMDB_DIR = RESULTS_DIR + "lmdb/"

# These folders get appended to the respective train/val/test directory
ORIGINAL_SUBDIR = "original_images/"
GT_SUBDIR = "processed_gt/"
RECALL_SUBDIR = "recall_weights/"
PRECISION_SUBDIR = "precision_weights/"

random.seed('hello')

def debug_print(string):
    if __debug__:
        print("DEBUG: {}".format(string))

def convert(args):
    file = args[0]
    grayscale = args[1]

    if "gt" in file:
        return

    file = ORIGINAL_DIR + file
    gt_file = file[:-4] + "_gt" + file[-4:]

    original = cv2.imread(file)
    gt = cv2.imread(gt_file)

    if original.shape[0] < 256 or original.shape[1] < 256:
        return

    print("Croppping and prepping {} {}".format(file, original.shape))

    for x in range(5):
        top_left_x = random.randint(0, original.shape[0] - 256)
        top_left_y = random.randint(0, original.shape[1] - 256)
        bottom_right_x = top_left_x + 256
        bottom_right_y = top_left_y + 256

        old_original = original.copy()
        old_gt = gt.copy()

        original = original[top_left_y:bottom_right_y, top_left_y:bottom_right_y]
        gt = gt[top_left_y:bottom_right_y, top_left_y:bottom_right_y]
        gt = gt[:,:,1]
        gt = np.clip(gt, 0, 1)
        gt = 1 - gt

        edges = cv2.Canny(original, 100, 200)

        pixel_count = cv2.countNonZero(edges)

        if pixel_count > 0.01 * original.shape[0] * original.shape[1]:
            break
        elif x == 4:
            file = GARBAGE_DIR + os.path.basename(file)
            gt_file = GARBAGE_DIR + os.path.basename(gt_file)


            cv2.imwrite(file, original)
            cv2.imwrite(gt_file, gt)
            return

        original = old_original
        gt = old_gt

    file = FULL_DIR + ORIGINAL_SUBDIR + os.path.basename(file)
    gt_file = FULL_DIR + GT_SUBDIR + os.path.basename(file)
    recall_file = FULL_DIR + RECALL_SUBDIR + os.path.basename(file)
    precision_file = FULL_DIR + PRECISION_SUBDIR + os.path.basename(file)

    weighted_image = 128 * np.ones_like(original)
    weighted_image = weighted_image[:,:,1]

    if grayscale == True:
        original = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)

    cv2.imwrite(file, original)
    cv2.imwrite(gt_file, gt)
    cv2.imwrite(recall_file, weighted_image)
    cv2.imwrite(precision_file, weighted_image)

def split_into_sets():

    # Since there is a 1:1 between the original files and each type of processed
    # image, we can just iterate over the original files and move each corresponding
    # processed image at the same time.
    files = os.listdir(FULL_DIR + ORIGINAL_SUBDIR)

    sequence = list(range(len(files)))
    random.shuffle(sequence)

    # Use 60% of data as training set, 20% as validation set, and 20% as test set
    train_cut_off = len(files) // (1 / .6)
    val_cut_off = len(files) // (1 / .8)

    # Go through generated images and seperate them into three folders for
    # training, testing, and validation
    for count, index in enumerate(sequence):

        file = files[index]

        if count < train_cut_off:
            shutil.move(FULL_DIR + ORIGINAL_SUBDIR + file, TRAIN_DIR + ORIGINAL_SUBDIR + file)
            shutil.move(FULL_DIR + GT_SUBDIR + file, TRAIN_DIR + GT_SUBDIR + file)
            shutil.move(FULL_DIR + RECALL_SUBDIR + file, TRAIN_DIR + RECALL_SUBDIR + file)
            shutil.move(FULL_DIR + PRECISION_SUBDIR + file, TRAIN_DIR + PRECISION_SUBDIR + file)
        elif count < val_cut_off:
            shutil.move(FULL_DIR + ORIGINAL_SUBDIR + file, VAL_DIR + ORIGINAL_SUBDIR + file)
            shutil.move(FULL_DIR + GT_SUBDIR + file, VAL_DIR + GT_SUBDIR + file)
            shutil.move(FULL_DIR + RECALL_SUBDIR + file, VAL_DIR + RECALL_SUBDIR + file)
            shutil.move(FULL_DIR + PRECISION_SUBDIR + file, VAL_DIR + PRECISION_SUBDIR + file)
        else:
            shutil.move(FULL_DIR + ORIGINAL_SUBDIR + file, TEST_DIR + ORIGINAL_SUBDIR + file)
            shutil.move(FULL_DIR + GT_SUBDIR + file, TEST_DIR + GT_SUBDIR + file)
            shutil.move(FULL_DIR + RECALL_SUBDIR + file, TEST_DIR + RECALL_SUBDIR + file)
            shutil.move(FULL_DIR + PRECISION_SUBDIR + file, TEST_DIR + PRECISION_SUBDIR + file)



    # Generate Label files
    for dir in [ ("train", TRAIN_DIR), ("test", TEST_DIR), ("val", VAL_DIR) ]:
        with open("{}{}.txt".format(LABELS_DIR, dir[0]), 'w') as output:

            for file in os.listdir(dir[1] + ORIGINAL_SUBDIR):

                output.write("./{}\n".format(file))

def process_im(im_file):
    im = cv2.imread(im_file, cv2.IMREAD_UNCHANGED)
    return im


def open_db(db_file):
    env = lmdb.open(db_file, readonly=False, map_size=int(2 ** 38), writemap=False, max_readers=10000)
    txn = env.begin(write=True)
    return env, txn

def package(im, encoding='png'):
    doc_datum = caffe.proto.caffe_pb2.DocumentDatum()
    datum_im = doc_datum.image

    datum_im.channels = im.shape[2] if len(im.shape) == 3 else 1
    datum_im.width = im.shape[1]
    datum_im.height = im.shape[0]
    datum_im.encoding = 'png'

    # image data
    if encoding != 'none':
        buf = io.BytesIO()
        if datum_im.channels == 1:
            plt.imsave(buf, im, format=encoding, vmin=0, vmax=255, cmap='gray')
        else:
            plt.imsave(buf, im, format=encoding, vmin=0, vmax=1)
        datum_im.data = buf.getvalue()
    else:
        pix = im.transpose(2, 0, 1)
        datum_im.data = pix.tostring()

    return doc_datum

def create_lmdb(images, db_file):
    env, txn = open_db(db_file)
    for x, imname in enumerate(os.listdir(images)):
        if x and x % 10 == 0:
            print ("Processed {} images".format(x))
        try:
            im_file = os.path.join(images, imname)
            im = process_im(im_file)
            # remove patches containing all background
            # if remove_background:
                # idx = 0
                # while idx < len(ims):
                    # gt = gts[idx]
                    # if gt.max() == 0:
                        # #print "Deleting patch %d of image %s" % (idx, im_file)
                        # del gts[idx]
                        # del ims[idx]
                    # else:
                        # idx += 1

            doc_datum = package(im)

            key = "%d:%d:%s" % (76547000 + x * 37, x, os.path.splitext(os.path.basename(im_file))[0])
            # TODO - is this the right encode direction?
            txn.put(key.encode(), doc_datum.SerializeToString())
            if x % 10 == 0:
                txn.commit()
                env.sync()
                print(env.stat())
                print(env.info())
                txn = env.begin(write=True)

        except Exception as e:
            print(e)
            print(traceback.print_exc(file=sys.stdout))
            print("Error occured on:", im_file)
            raise


    print("Done Processing Images")
    txn.commit()
    env.close()



# parser = argparse.ArgumentParser(description="Prepare generated images for training")
# parser.add_argument('data_dir', help="the directory in which the base images reside")
# parser.add_argument('--overwrite-crops', help="crop images, even if cropped images already exist")
# parser.add_argument('--overwrite-val-and-train', help="seperate into val and train, even if directories already exist")
# parser.add_argument('--overwrite-all', help="overwrite everything, no matter what work has already been done")
# parser.add_argument('--grayscale', help="render final images in grayscale")
# parser.add_argument('--color', help="render final images in color")
# parser.parse_args()

print("Source Dir: {}".format(ORIGINAL_DIR))
print("Results Dir: {}".format(RESULTS_DIR))

shutil.rmtree(RESULTS_DIR)

# STEP 0 - Make sure needed directories all exist
for dir in [ FULL_DIR, TRAIN_DIR, VAL_DIR, TEST_DIR ]:
    for subdir in [ ORIGINAL_SUBDIR, GT_SUBDIR, RECALL_SUBDIR, PRECISION_SUBDIR ]:
        try:
            debug_print("Creating folder: {}".format(dir + subdir))
            os.makedirs(dir + subdir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

try:
    debug_print("Creating folder: {}".format(LABELS_DIR))
    os.makedirs(LABELS_DIR)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

pool = Pool()

# STEP 1 - Resize the original images and generate auxiliary files
print("-- Starting STEP 1 --")
pool.map(convert, list(map(lambda x: [x, True], os.listdir(ORIGINAL_DIR))))

# STEP 2 - Generate the recall and precision weights
print("-- Starting STEP 2 --")
split_into_sets()

# STEP 3 - Generate needed lmdb's
print("-- Starting STEP 3 --")

for dir in [ "train", "val", "test" ]:
    for subdir in [ ORIGINAL_SUBDIR, GT_SUBDIR, RECALL_SUBDIR, PRECISION_SUBDIR ]:
        # Strip off last slash
        type_name = subdir[:-1]

        lmdb_folder = "{}{}{}_{}_lmdb".format(LMDB_DIR, subdir, type_name, dir)

        try:
            debug_print("Creating folder: {}".format(lmdb_folder))
            os.makedirs(lmdb_folder)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

        create_lmdb(RESULTS_DIR + dir + "/" + subdir, lmdb_folder)

