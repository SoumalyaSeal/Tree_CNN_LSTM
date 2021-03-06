from __future__ import print_function
import torch.backends.cudnn as cudnn
cudnn.benchmark = True # make something faster
import os, time, argparse
from tqdm import tqdm
import numpy
import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable as Var
import utils
import gc
import sys
from meowlogtool import log_util



# IMPORT CONSTANTS
import Constants
# NEURAL NETWORK MODULES/LAYERS
from model import *
# DATA HANDLING CLASSES
from tree import Tree
from vocab import Vocab
# DATASET CLASS FOR SICK DATASET
from dataset import SSTDataset, SeqSSTDataset
# METRICS CLASS FOR EVALUATION
from metrics import Metrics
# UTILITY FUNCTIONS
from utils import load_word_vectors, build_vocab
# CONFIG PARSER
from config import parse_args
# TRAIN AND TEST HELPER FUNCTIONS
from trainer import SentimentTrainer
from multichannel_trainer import MultiChannelSentimentTrainer
import numpy as np

# MAIN BLOCK
def main():
    global args
    args = parse_args(type=1)
    print (args.name)
    print (args.model_name)

    if args.mem_dim == 0:
        if args.model_name == 'dependency':
            args.mem_dim = 168
        elif args.model_name == 'constituency':
            args.mem_dim = 150
        elif args.model_name == 'lstm':
            args.mem_dim = 168
        elif args.model_name == 'bilstm':
            args.mem_dim = 168

    if args.num_classes == 0:
        if args.fine_grain:
            args.num_classes = 5 # 0 1 2 3 4
        else:
            args.num_classes = 3 # 0 1 2 (1 neutral)
    elif args.num_classes == 2:
        # assert False # this will not work
        assert not args.fine_grain

    args.cuda = args.cuda and torch.cuda.is_available()
    # args.cuda = False
    print(args)
    # torch.manual_seed(args.seed)
    # if args.cuda:
        # torch.cuda.manual_seed(args.seed)

    train_dir = os.path.join(args.data,'train/')
    dev_dir = os.path.join(args.data,'dev/')
    test_dir = os.path.join(args.data,'test/')

    # write unique words from all token files
    token_files = [os.path.join(split, 'sents.toks') for split in [train_dir, dev_dir, test_dir]]
    #
    vocab_file = os.path.join(args.data,'vocab-cased.txt') # use vocab-cased
    if not os.path.isfile(vocab_file):
        build_vocab(token_files, vocab_file)
    # build_vocab(token_files, vocab_file) NO, DO NOT BUILD VOCAB,  USE OLD VOCAB

    # get vocab object from vocab file previously written
    vocab = Vocab(filename=vocab_file)
    # vocab.add(Constants.UNK)

    print('==> SST vocabulary size : %d ' % vocab.size())

    # Load SST dataset splits

    is_preprocessing_data = False # let program turn off after preprocess data

    if args.model_name == 'dependency' or args.model_name == 'constituency':
        DatasetClass = SSTDataset
    elif args.model_name == 'lstm' or args.model_name == 'bilstm':
        DatasetClass = SeqSSTDataset


    # train
    train_file = os.path.join(args.data,'sst_train.pth')
    if os.path.isfile(train_file):
        train_dataset = torch.load(train_file)
    else:
        train_dataset = DatasetClass(train_dir, vocab, args.num_classes, args.fine_grain, args.model_name)
        torch.save(train_dataset, train_file)
        is_preprocessing_data = True

    # dev
    dev_file = os.path.join(args.data,'sst_dev.pth')
    if os.path.isfile(dev_file):
        dev_dataset = torch.load(dev_file)
    else:
        dev_dataset = DatasetClass(dev_dir, vocab, args.num_classes, args.fine_grain, args.model_name)
        torch.save(dev_dataset, dev_file)
        is_preprocessing_data = True

    # test
    test_file = os.path.join(args.data,'sst_test.pth')
    if os.path.isfile(test_file):
        test_dataset = torch.load(test_file)
    else:
        test_dataset = DatasetClass(test_dir, vocab, args.num_classes, args.fine_grain, args.model_name)
        torch.save(test_dataset, test_file)
        is_preprocessing_data = True

    criterion = nn.NLLLoss()
    # initialize model, criterion/loss_function, optimizer
    if args.embedding == 'multi_channel':
        args.channel = 2
        embedding_model2 = nn.Embedding(vocab.size(), args.input_dim)
    else:
        args.channel = 1

    if args.model_name == 'dependency' or args.model_name == 'constituency':
        model = TreeLSTMSentiment(
                    args.cuda, args.channel,
                    args.input_dim, args.mem_dim,
                    args.num_classes, args.model_name, criterion
                )
    elif args.model_name == 'lstm' or args.model_name == 'bilstm':
        model = LSTMSentiment(
                    args.cuda, args.channel,
                    args.input_dim, args.mem_dim,
                    args.num_classes, args.model_name, criterion,
                    pooling=args.pooling
                )

    embedding_model = nn.Embedding(vocab.size(), args.input_dim)

    if args.cuda:
        embedding_model = embedding_model.cuda()
        if args.channel ==2:
            embedding_model2 = embedding_model2.cuda()

    if args.cuda:
        model.cuda(), criterion.cuda()


    # for words common to dataset vocab and GLOVE, use GLOVE vectors
    # for other words in dataset vocab, use random normal vectors
    emb_split_token = ' '
    if args.embedding == 'glove':
        emb_torch = 'sst_embed.pth'
        emb_vector = 'glove.840B.300d'
        emb_vector_path = os.path.join(args.glove, emb_vector)
        # assert os.path.isfile(emb_vector_path+'.txt')
    elif args.embedding == 'paragram':
        emb_torch = 'sst_embed_paragram.pth'
        emb_vector = 'paragram_300_sl999'
        emb_vector_path = os.path.join(args.paragram, emb_vector)
        assert os.path.isfile(emb_vector_path+'.txt')
    elif args.embedding == 'paragram_xxl':
        emb_torch = 'sst_embed_paragram_xxl.pth'
        emb_vector = 'paragram-phrase-XXL'
        emb_vector_path = os.path.join(args.paragram, emb_vector)
        assert os.path.isfile(emb_vector_path + '.txt')
    elif args.embedding == 'other':
        emb_torch = 'other.pth'
        emb_vector = args.embedding_other
        emb_vector_path = emb_vector
        emb_split_token = '\t'
        assert os.path.isfile(emb_vector_path + '.txt')
    elif args.embedding == 'multi_channel':
        emb_torch = 'sst_embed1.pth'
        emb_torch2 = 'sst_embed2.pth'
        emb_vector_path = args.embedding_other
        emb_vector_path2 = args.embedding_othert
        assert os.path.isfile(emb_vector_path + '.txt')
        assert os.path.isfile(emb_vector_path2 + '.txt')
        assert (os.path.isfile(emb_vector_path2 + '.txt'), emb_vector_path2)
    else:
        assert False

    emb_file = os.path.join(args.data, emb_torch)
    if os.path.isfile(emb_file):
        emb = torch.load(emb_file)
        print('load %s' % (emb_file))
    else:
        # load glove embeddings and vocab
        glove_vocab, glove_emb = load_word_vectors(emb_vector_path, emb_split_token)
        print('==> Embedding vocabulary size: %d ' % glove_vocab.size())

        emb = torch.zeros(vocab.size(),glove_emb.size(1))

        for word in vocab.labelToIdx.keys():
            if glove_vocab.getIndex(word):
                emb[vocab.getIndex(word)] = glove_emb[glove_vocab.getIndex(word)]
            else:
                emb[vocab.getIndex(word)] = torch.Tensor(emb[vocab.getIndex(word)].size()).normal_(-0.05,0.05)
        # torch.save(emb, emb_file)
        glove_emb = None
        glove_vocab = None
        gc.collect()
        # add pretrain embedding
        # pretrain embedding would overwrite exist embedding from glove
        embed1_txt = os.path.join(args.state_dir, 'embed1')
        if os.path.isfile(embed1_txt+'.txt'):
            print ('load %s'%(embed1_txt))
            glove_vocab, glove_emb = load_word_vectors(embed1_txt, emb_split_token)
            print('==> embed1 vocabulary size: %d ' % glove_vocab.size())
            for word in vocab.labelToIdx.keys():
                if glove_vocab.getIndex(word):
                    emb[vocab.getIndex(word)] = glove_emb[glove_vocab.getIndex(word)]
                else:
                    emb[vocab.getIndex(word)] = torch.Tensor(emb[vocab.getIndex(word)].size()).normal_(-0.05, 0.05)
        torch.save(emb, emb_file) # saved word embedding matrix

        is_preprocessing_data = True # flag to quit
        print('done creating emb, quit')

    if args.embedding == 'multi_channel':
        emb_file2 = os.path.join(args.data, emb_torch2)
        if os.path.isfile(emb_file2):
            emb2 = torch.load(emb_file2)
            print ('load %s'%(emb_file2))
        else:
            # load glove embeddings and vocab
            glove_vocab, glove_emb = load_word_vectors(emb_vector_path2, emb_split_token)
            print('==> Embedding vocabulary size: %d ' % glove_vocab.size())

            emb2 = torch.zeros(vocab.size(), glove_emb.size(1))

            for word in vocab.labelToIdx.keys():
                if glove_vocab.getIndex(word):
                    emb2[vocab.getIndex(word)] = glove_emb[glove_vocab.getIndex(word)]
                else:
                    emb2[vocab.getIndex(word)] = torch.Tensor(emb2[vocab.getIndex(word)].size()).normal_(-0.05, 0.05)

            embed2_txt = os.path.join(args.state_dir, 'embed2')
            if os.path.isfile(embed2_txt + '.txt'):
                print('load %s' % (embed2_txt))
                glove_vocab, glove_emb = load_word_vectors(embed2_txt, emb_split_token)
                print('==> embed1 vocabulary size: %d ' % glove_vocab.size())
                for word in vocab.labelToIdx.keys():
                    if glove_vocab.getIndex(word):
                        emb2[vocab.getIndex(word)] = glove_emb[glove_vocab.getIndex(word)]
                    else:
                        emb2[vocab.getIndex(word)] = torch.Tensor(emb2[vocab.getIndex(word)].size()).normal_(-0.05, 0.05)
            torch.save(emb2, emb_file2)
            glove_emb = None
            glove_vocab = None
            gc.collect()
            is_preprocessing_data = True  # flag to quit
            print('done creating emb, quit')

    if is_preprocessing_data:
        print ('quit program')
        quit()

    # plug these into embedding matrix inside model
    if args.cuda:
        emb = emb.cuda()
        if args.channel == 2:
            emb2 = emb2.cuda()
    embedding_model.state_dict()['weight'].copy_(emb)
    if args.channel == 2:
        embedding_model2.state_dict()['weight'].copy_(emb2)

    # load cnn, lstm state_dict here
    if args.state_dir != 'meow': #TODO: here
        model.load_state_files(args.state_dir)

    if args.optim=='adam':
        optimizer   = optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.wd)
    elif args.optim=='adagrad':
        # optimizer   = optim.Adagrad(filter(lambda p: p.requires_grad, model.parameters()), lr=args.lr, weight_decay=args.wd)
        optimizer = optim.Adagrad(model.parameters(), lr=args.lr, weight_decay=args.wd)
    elif args.optim == 'adadelta':
        optimizer = optim.Adadelta(model.parameters(), lr = args.lr, weight_decay=args.wd)
    elif args.optim=='adam_combine':
        optimizer = optim.Adam([
                {'params': model.parameters(), 'lr':args.lr, 'weight_decay':args.wd },
                {'params': embedding_model.parameters(), 'lr': args.emblr, 'weight_decay':args.embwd}
            ])
        args.manually_emb = 0
    elif args.optim == 'adagrad_combine':
        optimizer = optim.Adagrad([
                {'params': model.parameters(), 'lr':args.lr, 'weight_decay':args.wd },
                {'params': embedding_model.parameters(), 'lr': args.emblr, 'weight_decay':args.embwd}
            ])
        args.manually_emb = 0
    elif args.optim == 'adam_combine_v2':
        model.embedding_model = embedding_model
        optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.wd)
        args.manually_emb = 0
    metrics = Metrics(args.num_classes)
    utils.count_param(model)

    # create trainer object for training and testing
    # if args.model_name == 'dependency' or args.model_name == 'constituency':
    #     trainer = SentimentTrainer(args, model, embedding_model, criterion, optimizer)
    # elif args.model_name == 'lstm' or args.model_name == 'bilstm':
    #     trainer = SentimentTrainer(args, model, embedding_model, criterion, optimizer)

    if args.channel ==1:
        # trainer = MultiChannelSentimentTrainer(args, model, [embedding_model], criterion, optimizer)
        trainer = SentimentTrainer(args, model, embedding_model, criterion, optimizer)
    else:
        trainer = MultiChannelSentimentTrainer(args, model, [embedding_model, embedding_model2], criterion, optimizer)

    trainer.set_initial_emb(emb)

    # trainer = SentimentTrainer(args, model, embedding_model ,criterion, optimizer)

    test_idx_dir = os.path.join(args.data, args.test_idx)
    test_idx = None
    if os.path.isfile(test_idx_dir):
        print('load test idx %s' % (args.test_idx))
        test_idx = np.load(test_idx_dir)

    mode = args.mode
    dev_loss, dev_pred, _ = trainer.test(dev_dataset) # make sure thing go smooth before train
    dev_acc = metrics.sentiment_accuracy_score(dev_pred, dev_dataset.labels)
    print('==> Dev loss   : %f \t' % dev_loss, end="")
    print('before run dev percentage ', dev_acc)
    if mode == 'DEBUG':
        for epoch in range(args.epochs):
            # print a tree
            tree, sent, label = dev_dataset[3]
            utils.print_span(tree, sent, vocab)
            quit()

            dev_loss = trainer.train(dev_dataset)
            dev_loss, dev_pred, _ = trainer.test(dev_dataset)
            test_loss, test_pred, _ = trainer.test(test_dataset)

            dev_acc = metrics.sentiment_accuracy_score(dev_pred, dev_dataset.labels)
            # test_acc = metrics.sentiment_accuracy_score(test_pred, test_dataset.labels)
            print('==> Dev loss   : %f \t' % dev_loss, end="")
            print('Epoch ', epoch, 'dev percentage ', dev_acc)
    elif mode == "PRINT_TREE":
        file_path = os.path.join('print_tree',args.name+'.npy')
        print_list = np.load(file_path)
        utils.print_trees_file_v2(args, vocab, test_dataset, print_list, name='tree')
        print('break')
        quit()
    elif mode == 'EVALUATE':
        print ('EVALUATION')
        print ('--Model information--')
        print (model)
        filename = args.name + '.pth'
        model = torch.load(os.path.join(args.saved,'_model_' + filename))
        embedding_model = torch.load(os.path.join(args.saved, '_embedding_' + filename))
        if args.channel == 1:
            trainer = SentimentTrainer(args, model, embedding_model, criterion, optimizer)
        elif args.channel ==2:
            embedding_model2 = torch.load(os.path.join(args.saved, '_embedding2_' + filename))
            trainer = MultiChannelSentimentTrainer(args, model, [embedding_model, embedding_model2], criterion,
                                                   optimizer)

        test_loss, test_pred, subtree_metrics = trainer.test(test_dataset)
        test_acc = metrics.sentiment_accuracy_score(test_pred, test_dataset.labels, num_classes=args.num_classes)
        print(' |test percentage ' + str(test_acc))
        result_filename = os.path.join(args.logs,args.name) + 'result.txt'
        rwriter = open(result_filename, 'w')
        for i in range(test_pred.size()[0]):
            rwriter.write(str(int(test_pred[i]))+' '+str(int(test_dataset.labels[i]))+ '\n')
        rwriter.close()
        result_link = log_util.up_gist(result_filename, args.name, __file__,
                                    client_id='ec3ce6baf7dad6b7cf2c',
                                    client_secret='82240b38a7e662c28b2ca682325d634c9059efb0')
        print(result_link)

        print_list = subtree_metrics.print_list
        utils.print_trees_file_all(args, vocab, test_dataset, print_list, name='Tree')
        print('____________________' + str(args.name) + '___________________')
    elif mode == "EXPERIMENT":
        print ('--Model information--')
        print (model)
        # dev_loss, dev_pred = trainer.test(dev_dataset)
        # dev_acc = metrics.sentiment_accuracy_score(dev_pred, dev_dataset.labels, num_classes=args.num_classes)
        max_dev = 0
        max_dev_epoch = 0
        filename = args.name + '.pth'
        for epoch in range(args.epochs):
            train_loss_while_training = trainer.train(train_dataset)
            if epoch%5 == 0:# save at least 1 hours
                train_loss, train_pred, _ = trainer.test(train_dataset)
                train_acc = metrics.sentiment_accuracy_score(train_pred, train_dataset.labels, num_classes=args.num_classes)
                print('Train acc %f ' % (train_acc))
            dev_loss, dev_pred, _ = trainer.test(dev_dataset)
            dev_acc = metrics.sentiment_accuracy_score(dev_pred, dev_dataset.labels, num_classes=args.num_classes)
            print('==> Train loss   : %f \t' % train_loss_while_training, end="")
            print('Epoch ', epoch, 'dev percentage ', dev_acc)
            print ('Epoch %d dev percentage %f ' %(epoch, dev_acc))

            if dev_acc > max_dev:
                print ('update best dev acc %f ' %(dev_acc))
                max_dev = dev_acc
                max_dev_epoch = epoch
                utils.mkdir_p(args.saved)
                torch.save(model, os.path.join(args.saved, '_model_' + filename))
                torch.save(embedding_model, os.path.join(args.saved, '_embedding_' + filename))
                if args.channel ==2:
                    torch.save(embedding_model2, os.path.join(args.saved, '_embedding2_' + filename))
            gc.collect()
        print('epoch ' + str(max_dev_epoch) + ' dev score of ' + str(max_dev))
        print('eva on test set ')

        model = torch.load(os.path.join(args.saved,'_model_' + filename))
        embedding_model = torch.load(os.path.join(args.saved, '_embedding_' + filename))
        if args.channel == 1:
            trainer = SentimentTrainer(args, model, embedding_model, criterion, optimizer)
        elif args.channel ==2:
            embedding_model2 = torch.load(os.path.join(args.saved, '_embedding2_' + filename))
            trainer = MultiChannelSentimentTrainer(args, model, [embedding_model, embedding_model2], criterion,
                                                   optimizer)

        test_loss, test_pred, subtree_metrics = trainer.test(test_dataset)
        test_acc = metrics.sentiment_accuracy_score(test_pred, test_dataset.labels, num_classes=args.num_classes)
        print('Epoch with max dev:' + str(max_dev_epoch) + ' |test percentage ' + str(test_acc))
        print_list = subtree_metrics.print_list
        torch.save(print_list, os.path.join(args.saved, args.name + 'printlist.pth'))
        utils.print_trees_file(args, vocab, test_dataset, print_list, name='tree')
        print('____________________' + str(args.name) + '___________________')
    else:
        for epoch in range(args.epochs):
            train_loss = trainer.train(train_dataset)
            train_loss, train_pred, _ = trainer.test(train_dataset)
            dev_loss, dev_pred, _ = trainer.test(dev_dataset)
            test_loss, test_pred, subtree_metrics = trainer.test(test_dataset)

            train_acc = metrics.sentiment_accuracy_score(train_pred, train_dataset.labels)
            dev_acc = metrics.sentiment_accuracy_score(dev_pred, dev_dataset.labels)
            test_acc = metrics.sentiment_accuracy_score(test_pred, test_dataset.labels)
            print('==> Train loss   : %f \t' % train_loss, end="")
            print('Epoch ', epoch, 'train percentage ', train_acc)
            print('Epoch ', epoch, 'dev percentage ', dev_acc)
            print('Epoch ', epoch, 'test percentage ', test_acc)
            print_list = subtree_metrics.print_list
            torch.save(print_list, os.path.join(args.saved, args.name + 'printlist.pth'))
            utils.print_trees_file(args, vocab, test_dataset, print_list, name='tree')


if __name__ == "__main__":
    args = parse_args(type=1)
    # log to console and file
    log_name = os.path.join(args.logs,args.name)
    utils.mkdir_p(args.logs)  # create folder if not exist
    logger1 = log_util.create_logger(log_name, print_console=True)
    logger1.info("LOG_FILE") # log using loggerba
    # attach log to stdout (print function)
    s1 = log_util.StreamToLogger(logger1)
    sys.stdout = s1
    print ('_________________________________start___________________________________')
    main()
    log_link = log_util.up_gist(os.path.join(args.logs,args.name+'.log'), args.name, __file__, client_id='ec3ce6baf7dad6b7cf2c', client_secret='82240b38a7e662c28b2ca682325d634c9059efb0')
    print(log_link)