import argparse
import os
import scipy.io.wavfile as wav
import numpy as np
import json

from fairseq import options
from speech_detecting import SpeechDetecting
from language_identify import LanguageIdentify
from error_codes import *


class AudioLID:

    def __init__(self,
                 language_model,
                 lang_dict_dir,
                 debug=False,
                 output_path='./temp',
                 speech_segment_count=5,
                 speech_segment_duration=5,
                 speech_score_threshold=0.7,
                 parse_start_offset=60,
                 top_k=3,
                 denoise_model=None):
        np.random.seed(123)
        args_parser = options.get_generation_parser(default_task="audio_classification")
        input_args = []
        input_args.append(lang_dict_dir)
        input_args.append('--path')
        input_args.append(language_model)
        self.args = options.parse_args_and_arch(args_parser, input_args=input_args)
        self.args.debug = debug
        self.args.output_path = output_path
        self.args.speech_segment_count = speech_segment_count
        self.args.speech_segment_duration = speech_segment_duration
        self.args.speech_score_threshold = speech_score_threshold
        self.args.parse_start_offset = parse_start_offset
        self.args.top_k = top_k
        self.args.denoise_model = denoise_model
        self.language_identify = LanguageIdentify(self.args)
        self.speech_detecting = SpeechDetecting(self.args)

    def infer_language(self, audio_file):
        """
        :param audio_file: The audio url needs to detect the language,
                support both local file paths and online URLs
                support both mp4 and mp3 formats
        :return: ret the result, if big then zero mean successful, otherwise error,
                the error code refer to error_codes.py
                language_list: the language list inferred by the given audio file is sorted by score
                format likes: [('eng', 90.0), ('ch', 10.0)]
                            or [('eng', 100.0)],
                            the total score always equal 100.
                The language short name like 'eng' to full name map:https://dl.fbaipublicfiles.com/mms/lid/mms1b_l126_langs.html
        """
        ret, samples = self.speech_detecting.load_audio_samples(audio_file)
        if ret < 0:
            print(f'load audio file failed, ret:{ret}')
            exit(1)
        ret, speech_list = self.speech_detecting.find_speech_list(samples, self.args.speech_score_threshold,
                                                                  self.args.speech_segment_duration)
        print(f'find speech result code:{ret}')

        if 0 <= ret < self.args.speech_segment_count:
            # try again
            ret, speech_list = self.speech_detecting.find_speech_list(samples, max(self.args.speech_score_threshold - 0.2, 0.4),
                                                                      max(self.args.speech_segment_duration - 2, 3))
            print(f'try to find speech result code:{ret}')

        if self.args.debug:
            if ret > 0:
                dir_path = self.args.output_path
                print(f'save audio seg and manifest file to dir:{dir_path}')
                if not os.path.exists(dir_path):
                    os.makedirs(dir_path)
                index = 0
                file_path_list = []
                for speech_obj in speech_list:
                    print(f'speech_obj:{speech_obj}')
                    index += 1
                    file_path = os.path.join(dir_path, f'index_{index}.wav')
                    wav.write(file_path, 16000, speech_obj.samples)
                    file_path_list.append(os.path.abspath(file_path))

                manifest_tsv_file = os.path.join(dir_path, 'manifest.tsv')
                manifest_lang_file = os.path.join(dir_path, 'manifest.lang')
                with open(manifest_tsv_file, mode='w+', encoding='utf-8') as f:
                    f.write("/\n")
                    for file_path in file_path_list:
                        f.write(f'{file_path}\t16000\n')
                with open(manifest_lang_file, mode='w+', encoding='utf-8') as f:
                    for _ in file_path_list:
                        f.write('eng\t1\n')

        if ret < 0:
            return ret, None
        predictions = self.language_identify.infer([speech_obj.samples for speech_obj in speech_list])
        if self.args.debug:
            print(f'prediction origin result:{predictions}')

        total_score = 0
        min_threshold = 0.4
        valid_count = 0
        language_score_map = {}
        for key, prediction in predictions.items():
            for language_str, score in prediction:
                if score < min_threshold:
                    continue
                valid_count += 1
                total_score += score

                language_total_score = 0
                if language_str in language_score_map:
                    language_total_score = language_score_map[language_str]
                language_total_score += score
                language_score_map[language_str] = language_total_score

        if self.args.debug:
            print(f'language resort map:{language_score_map}')
        result_list = []
        if len(language_score_map) == 0:
            return ERROR_CODE_NO_VALID_LANGUAGE, None

        for language_str, score in language_score_map.items():
            result_list.append((language_str, float(score * 100 / total_score)))
        result_list = sorted(result_list, key=lambda language_obj: language_obj[1], reverse=True)

        if self.args.debug:
            print(f'result_list:{result_list}')
        return valid_count, result_list


if __name__ == '__main__':
    # audio_file = "//dataset/test_100/mp3/a03f2c4780798a5398c86b196e479275.mp3"
    audio_file = "http://vfx.mtime.cn/Video/2019/06/27/mp4/190627231412433967.mp4"
    parser = argparse.ArgumentParser(add_help=True)
    # speech detecting
    parser.add_argument(
        '--audio-file', type=str, default=audio_file,
        help='Audio file to detect language, support local file and online url')
    parser.add_argument(
        '--debug', type=bool, default=True,
        help='Debug mode.')
    parser.add_argument(
        '--speech-segment-count', type=int, default=5,
        help='The segment count need to detect language, default:5')
    parser.add_argument(
        '--speech-segment-duration', type=int, default=5,
        help='The length of each speech segment, unit:second, default:5')
    parser.add_argument(
        '--speech-score-threshold', type=float, default=0.7, help='The threshold, range:0-1,unit:float, default:0.7')
    parser.add_argument(
        '--parse-start-offset', type=int, default=60,
        help='The file start offset that need to skip, unit:second, default:60')
    parser.add_argument(
        '--output-path', type=str, default='./temp',
        help='The output dir path use to save temp file in debug mode, default:./temp')

    # language identify
    parser.add_argument("--infer-num-samples", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=3)

    parser.add_argument(
        '--language-model', type=str, default='./pretrain/mms1b_l126.pt',
        help='The model use to detect language class, default:./pretrain/mms1b_l126.pt')

    parser.add_argument(
        '--lang-dict-dir', type=str, default='./pretrain',
        help='The dir contains the file of language, default:./pretrain')

    parser.add_argument(
        '--denoise-model', type=str, default=None,
        help='The model use to denosie to make speech clear, default:None')
    args = parser.parse_args()

    lid = AudioLID(language_model=args.language_model, lang_dict_dir=args.lang_dict_dir, debug=args.debug,
                   speech_segment_count=args.speech_segment_count, speech_segment_duration=args.speech_segment_duration,
                   speech_score_threshold=args.speech_score_threshold, parse_start_offset=args.parse_start_offset,
                   top_k=args.top_k, denoise_model=args.denoise_model, output_path=args.output_path)
    ret, language_list = lid.infer_language(args.audio_file)

    if args.debug:
        with open(f"{args.output_path}/predictions.txt", "w") as fo:
            fo.write(f'ret:{ret}\n')
            if language_list is not None and len(language_list) > 0:
                fo.write(json.dumps(language_list) + "\n")
    print(f'infer result:{ret}, language list:{language_list}')






