"""
A synthetic handwritten Document

This module includes the Document class.
"""
import configparser
import errno
import multiprocessing
import os
import random
import subprocess
import sys
import shutil

import cv2
import image_util as util
import numpy as np

from lxml import etree
from text_writer_state import TextWriterState

CONFIG = configparser.ConfigParser()
CONFIG.read("settings.ini")

HANDWRITTEN_WORDS_DIR = CONFIG['DIRECTORIES']['handwritten_words_dir']
BACKGROUND_IMAGES_DIR = CONFIG['DIRECTORIES']['background_images_dir']
STAIN_IMAGES_DIR = CONFIG['DIRECTORIES']['stain_images_dir']
DEFAULT_BASE_OUTPUT_DIR = CONFIG['DIRECTORIES']['base_output_dir']

# /dev/shm should be mounted in RAM - allowing for fast IPC (Used as a
# consequence of using DivaDID.)
# If that does not work, just use /tmp
TMP_DIR = CONFIG['DIRECTORIES']['tmp_dir']

def dprint(*args, **kwargs):
    """
    A debug print function

    This is valuable as this program uses the multiprocessing library. This
    function will prepend every line with the currrent process number. (Note
    this is not the PID)
    """
    print(str(multiprocessing.current_process()._identity[0]) + ": "
          + " ".join(map(str, args)), **kwargs)


class Document:
    """
    A synthetic handwritten Document

    A Document instance is a synthetic, handwritten, text image. This class
    handles the generation of such images. It also has helper functions that
    allow for the saving of the generated images to disk.
    """

    def __init__(self, stain_level=1, noise_level=1,seed=None,
                 output_loc=DEFAULT_BASE_OUTPUT_DIR):
        """
        Initialize a new Document

        Parameters
        ----------
        seed : int, optional
            The random seed to use for this document
        stain_level : int, optional
            A value that is passed to DivaDID to determine amount of staining
        noise_level : int, optional
            A value that is passed to DivaDID to determine amount of noise
        output_loc : str, optional
            The location the final document will be saved to

        For every synthetic document created, a new Document object should
        be instantiated.

        A Document object is not guaranteed to be thread- or process-safe.
        However, the Document class itself is safe and different objects can
        be instantiated in different threads or processes. As long as no
        instance is accesed by more than one thread or process, all member
        functions can be safely called without concern about locks.
        """

        if not os.path.isdir(HANDWRITTEN_WORDS_DIR):
            raise OSError("{} folder for handwritten documents does not exist".format(HANDWRITTEN_WORDS_DIR))
        if not os.path.isdir(BACKGROUND_IMAGES_DIR):
            raise OSError("{} folder for background images does not exist".format(BACKGROUND_IMAGES_DIR))
        if not os.path.isdir(STAIN_IMAGES_DIR):
            raise OSError("{} folder for stain images does not exist".format(STAIN_IMAGES_DIR))

        self.stain_level = stain_level
        self.text_noisy_level = noise_level

        self.result = None
        self.result_ground_truth = None

        self.output_dir = output_loc
        dprint("Output_dir: {}".format(self.output_dir))

        if seed is not None:
            self.random_seed = seed
        else:
            self._assign_random_seed()

        # Seed both the python and numpy random number generators, so that we
        # can guarantee some sort of determinacy.
        random.seed(self.random_seed)
        np.random.seed(self.random_seed)

        dprint("Using seed {}".format(self.random_seed))

        self._gather_data_sources()

    def _assign_random_seed(self):
        """
        Get a random seed that will not clash with another document

        Since the random seed is used as part of the filename of the written
        document, there is value in making sure the seed of this document does
        not clash with another document that already exists in the target
        directory.
        """
        tries = 0
        condition = True

        random.seed()

        # This roughly emulates a do-while loop. We need to assign a seed at
        # least once.
        while condition:
            if tries > 10:
                raise RuntimeError("Could not find an unused seed")

            self.random_seed = random.randint(10000, 100000)

            file = "img_{}.png".format(self.random_seed)
            file = self.output_dir + '/' + file

            condition = os.path.isfile(file)

            tries += 1

    def _gather_data_sources(self):
        """ Parse lists of needed directories. """

        self.word_image_folder_list = [HANDWRITTEN_WORDS_DIR]
        return

        # self.word_image_folder_list = []

        # for hw_dir in os.listdir(HANDWRITTEN_WORDS_DIR):
        #     new_path = os.path.join(HANDWRITTEN_WORDS_DIR, hw_dir)

        #     files = os.listdir(new_path)

        #     for idx, item in enumerate(files):
        #         files[idx] = os.path.join(new_path, item)

        #     self.word_image_folder_list += files

    def create(self, bypass=False):
        """
        Generate a synthetic text document.

        Parameters
        ----------
        bypass : bool, optional
            Whether or not to bypass the DivaDID stage

        The current generation process has three stages. The first is to pick
        a random background image and then use DivaDID to apply some simply
        degradations to add some noise and natural variation.

        The second stage is to add text to the background image. During this
        process, the "ground truth" file is also created.

        The third and final stage is a second iteration of DivaDID. Now that we
        have text on the document, we degrade the image once more to give it
        a somewhat more realistic appearance.
        """

        base_working_dir = TMP_DIR

        # Get a random background image
        bg_image_name = random.choice(os.listdir(BACKGROUND_IMAGES_DIR))

        bg_full_path = os.path.join(BACKGROUND_IMAGES_DIR, bg_image_name)

        if bypass is True:
            dprint("Adding text to image {}".format(bg_full_path))
            img = cv2.imread(bg_full_path)
            if img is None:
                return
            if np.random.random() < 0.3:
                img = self._add_text_fade(img)
            img = self._add_text(img)

            filename = str(self.random_seed) + "_augmented.png"
            path = os.path.join(base_working_dir, filename)

            cv2.imwrite(path, img)

            self.result = path
            return

        # Generate XML for DivaDID and then degrade background image
        dprint("- Generating degraded image - pass 1")
        first_xml, first_image = self._generate_degradation_xml(bg_full_path,
                                                                1,
                                                                True,
                                                                base_working_dir)

        subprocess.check_call(["java", "-jar", "DivaDid.jar", first_xml],
                              stdout=subprocess.DEVNULL)

        # Add text to degraded background image
        dprint("-{} Adding text to image {} -".format(self.random_seed, bg_full_path))
        img = cv2.imread(first_image)
        if img is None:
            os.remove(first_xml)
            os.remove(first_image)
            return
        if np.random.random() < 0.3:
            img = self._add_text_fade(img)
        img = self._add_text(img)

        filename = str(self.random_seed) + "_augmented.png"
        path = os.path.join(base_working_dir, filename)
        cv2.imwrite(path, img)


        # Generate XML for second pass of DivaDID. Degrade image with text
        dprint("- Generating degraded image - pass 2")
        second_xml, second_image = self._generate_degradation_xml(
            path,
            2,
            True,
            base_working_dir)

        subprocess.check_call(["java", "-jar", "DivaDid.jar", second_xml],
                              stdout=subprocess.DEVNULL)

        self.result = second_image

        os.remove(first_xml)
        os.remove(second_xml)
        os.remove(first_image)

    def save(self, file=None):
        """
        Save the generated document to the passed location.

        Parameters
        ----------
        file : str, optional
            The name of the file to save the synthetic document to

        Note that due to the use of DivaDID, for performance reasons,
        intermediate stages of the document generation process are saved at
        /dev/shm. After everything is finished, the resulting product will
        likely need to be moved from that location to a final folder.
        """

        if self.result is None:
            dprint("Trying to save document before it has been generated.",
                   file=sys.stderr)
            return

        if file is None:
            file = "img_{}.png".format(self.random_seed)

        file = os.path.join(self.output_dir, file)

        try:
            os.makedirs(self.output_dir)
        except OSError as exception:
            if exception.errno != errno.EEXIST:
                raise

        shutil.copy2(self.result, file)
        dprint("File saved to {}".format(file))

        os.remove(self.result)

    def save_ground_truth(self, file=None):
        """
        Save the generated document to the passed location.

        Parameters
        ----------
        file : str, optional
            The name of the file to save the ground truth document to

        Note that due to the use of DivaDID, for performance reasons,
        intermediate stages of the document generation process are saved at
        /dev/shm. After everything is finished, the resulting product will
        likely need to be moved from that location to a final folder.
        """

        if self.result is None:
            dprint("Trying to save document before it has been generated.",
                   file=sys.stderr)
            return

        if file is None:
            file = "img_{}_gt.png".format(self.random_seed)

        file = os.path.join(self.output_dir, file)

        try:
            os.makedirs(self.output_dir)
        except OSError as exception:
            if exception.errno != errno.EEXIST:
                raise

        shutil.copy2(self.result_ground_truth, file)
        dprint("File saved to {}".format(file))

    def _add_text_fade(self, img):
        """
        Add faded text samples to given image.

        Parameters
        ----------
        img : cv2.Image
            The image to which to apply the faded text to

        Returns
        -------
        cv2.Image

        This function is a crude attempt to simulate the effect of pages that
        have text on both sides of the page. In many cases, the text on the back
        of the page bleeds through and is visible on the top. This makes
        binarization challenging.

        To attempt to create this effect, the generated text is heavily blurred
        and the intensity is reduced somewhat. This stage should happen before
        the real text stage.
        """

        color = np.array((53, 52, 46))

        state = TextWriterState(img.shape)

        word_rand_folder = random.choice(self.word_image_folder_list)

        all_words = None

        # Add individual words until we run out of space
        while True:

            word_image_name = random.choice(os.listdir(word_rand_folder))
            word_full_path = os.path.join(word_rand_folder, word_image_name)

            word = cv2.imread(word_full_path)
            new_word_space = np.full((word.shape[0] + 50, word.shape[1] + 50, 3),
                                     255,
                                     dtype=np.uint8)
            new_word_space[25:word.shape[0] + 25, 25:word.shape[1] + 25] = word
            word = new_word_space
            word = util.add_alpha_channel(word)

            # if word.shape[0] == 0 or word.shape[1] == 1:
                # continue

            # word = cv2.resize(word, (new_word_width, new_word_height), cv2.INTER_CUBIC)

            if state.get_next_word_pos(word.shape) is None:
                break

            color += np.random.randint(-2, 3, size=3)
            util.white_to_alpha(word, color=color)

            word = cv2.GaussianBlur(word, (51, 51), 0)

            # word = np.where((word - 20) < 0, 0, word - 20)

            all_words = state.get_padded_image(word)

        if all_words is not None:
            img = util.alpha_composite(img, all_words)

        return img

    def _add_text(self, img):
        """
        Add text samples to given image.

        Parameters
        ----------
        img : cv2.Image
            The image to which to apply the text to

        Returns
        -------
        cv2.Image

        This function works in much the same way as _add_faded_text. The
        general code execution follows the same path. The major difference is
        that this text will also become the ground truth text. So, at the same
        time as the text is generated and alpha blended onto the background,
        the ground truth image is generated as well.
        """

        color = np.array((53, 52, 46))

        state = TextWriterState(img.shape)

        ground_truth = np.ones((img.shape[0], img.shape[1], 3), np.uint8)

        word_rand_folder = random.choice(self.word_image_folder_list)

        all_words = None

        while True:

            word_image_name = random.choice(os.listdir(word_rand_folder))
            word_full_path = os.path.join(word_rand_folder, word_image_name)

            word = cv2.imread(word_full_path)
            word = util.add_alpha_channel(word)

            # if word.shape[0] == 0 or word.shape[1] == 1:
                # continue

            if state.get_next_word_pos(word.shape) is None:
                break

            color += np.random.randint(-2, 3, size=3)
            util.white_to_alpha(word, color=color)

            all_words = state.get_padded_image(word)

        if all_words is None:
            print("GOODBYE")
            return None

        ground_truth_word = all_words.copy()

        img = util.alpha_composite(img, all_words)
        ground_truth = util.alpha_composite(ground_truth, ground_truth_word)

        ground_truth = cv2.cvtColor(ground_truth, cv2.COLOR_BGR2GRAY)
        _, ground_truth = cv2.threshold(ground_truth, 10, 1, cv2.THRESH_BINARY)

        self.result_ground_truth = os.path.join(TMP_DIR, str(self.random_seed) + "_gt.png")

        cv2.imwrite(self.result_ground_truth, ground_truth)

        return img

    def _generate_degradation_xml(self,
                                  base_image,
                                  index=0,
                                  save=False,
                                  save_location=None):
        """
        Generate the XML needed by DivaDID to add surface stains to an image.

        Parameters
        ----------
        base_image : str
            The path of the image that DivaDID will apply degradations to
        index : int
            Used to differentiate between different DivaDID stages
        save : bool
            Whether to save the generated xml or not
        save_location : str
            The path the generated xml will be saved to

        Returns
        -------
        etree.Element
            The root element of the xml tree

        OR

        xml_full_pth : str
        output_full_pth : str

        This function takes the given base image and creates the XML that will
        be fed to DivaDID which specifies how to add a variety of surface
        stains and other imperfections.

        The generated XML can either be saved to the file system for analysis
        or further usage, or simply returned to be fed directly to DivaDID.

        In either case, the return value is the generated XML.
        """

        output_file_name = "degraded_{}_{}.png".format(self.random_seed, index)
        xml_file_name = "degradation_script_{}_{}.xml".format(self.random_seed,
                                                              index)

        stain_strength_low_bound = 0.1 * self.stain_level
        stain_strength_high_bound = 0.1 + 0.1 * self.stain_level
        stain_density_low_bound = 2 + 0.1 * self.stain_level
        stain_density_high_bound = 2 + 0.1 * self.stain_level

        if save_location is None:
            xml_full_path = os.path.join("data/xml/", xml_file_name)
            output_full_path = os.path.join("data/output/", output_file_name)
        else:
            xml_full_path = os.path.join(save_location, xml_file_name)
            output_full_path = os.path.join(save_location, output_file_name)

        root = etree.Element("root")

        alias_e = etree.SubElement(root, "alias")
        alias_e.set("id", "INPUT")

        alias_e.set("value", base_image)

        image_e = etree.SubElement(root, "image")
        image_e.set("id", "my-image")
        load_e = etree.SubElement(image_e, "load")
        load_e.set("file", "INPUT")

        image_e2 = etree.SubElement(root, "image")
        image_e2.set("id", "my-copy")
        copy_e2 = etree.SubElement(image_e2, "copy")
        copy_e2.set("ref", "my-image")

        # Add stains
        for stain_folder in [STAIN_IMAGES_DIR]:  # os.listdir(STAIN_IMAGES_DIR)[0:20]:
            gradient_degradation_e = etree.SubElement(root,
                                                      "gradient-degradations")
            gradient_degradation_e.set("ref", "my-copy")
            strength_e = etree.SubElement(gradient_degradation_e, "strength")
            strength_e.text = "{:.2f}".format(
                random.uniform(stain_strength_low_bound,
                               stain_strength_high_bound))
            density_e = etree.SubElement(gradient_degradation_e, "density")
            density_e.text = "{:.2f}".format(
                random.uniform(stain_density_low_bound,
                               stain_density_high_bound))
            iterations_e = etree.SubElement(gradient_degradation_e,
                                            "iterations")
            iterations_e.text = "750"
            source_e = etree.SubElement(gradient_degradation_e, "source")
            source_e.text = stain_folder

        save_e = etree.SubElement(root, "save")
        save_e.set("ref", "my-copy")
        save_e.set("file", output_full_path)

        if save is True:
            output_xml = open(xml_full_path, 'w')
            output_xml.write(
                etree.tostring(root, pretty_print=True).decode("utf-8"))

            return xml_full_path, output_full_path

        return root
