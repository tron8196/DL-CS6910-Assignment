import wandb
from ModelFactory import *
import tensorflow as tf
# wandb.login(key='677e9fea45b64f0b222413b502a3fbe87ea3b70e')

class Seq2SeqModel(tf.keras.Model):
    def __init__(self, rnn_type, num_encoder_layers, num_decoder_layers, embedding_dim, units,
                 dropout, attention_flag, teacher_forcing_flag):
        super(Seq2SeqModel, self).__init__()
        self.rnn_type = rnn_type
        self.num_encoder_layers = num_encoder_layers
        self.num_decoder_layers = num_decoder_layers
        self.embedding_dim = embedding_dim
        self.units = units
        self.dropout = dropout
        self.attention_flag = attention_flag
        self.history = []
        self.batch_size = 128
        self.teacher_forcing_flag = teacher_forcing_flag

    def build(self, loss, metric, optimizer):
        self.loss = loss
        self.metric = metric
        self.optimizer = optimizer

    @tf.function
    def train_step(self, input, target, encoder_state):
        loss = 0
        # all computations on graph where gradient is required should be written here.
        with tf.GradientTape() as tape:
            enc_output, enc_state = self.encoder(input, encoder_state)
            # output of encoder to go for decoder as input, whereas, state vector is used for context calculation
            dec_state = enc_state
            dec_input = tf.expand_dims([self.target_tokenizer.word_index["\t"]] * self.batch_size, 1)
            '''
            use correct target for previous step as input to the lstm instead of what got decoded, this reduces error propogation
            '''
            if self.teacher_forcing_flag:
                for t in range(1, target.shape[1]):
                    dec_output, dec_state, _ = self.decoder(dec_input, dec_state, enc_output)
                    loss += self.loss(target[:, t], dec_output)
                    dec_input = tf.expand_dims(target[:, t], 1)
                    self.metric.update_state(target[:, t], dec_output)
            else:
                for t in range(1, target.shape[1]):
                    dec_output, dec_state, _ = self.decoder(dec_input, dec_state, enc_output)
                    loss += self.loss(target[:, t], dec_output)
                    dec_input = tf.expand_dims(tf.argmax(dec_output, 1), 1)
                    self.metric.update_state(target[:, t], dec_output)

            batch_loss = loss / target.shape[1]
            '''
            model.variables returns a list, adding all the variables in the enc-dec model together in a single
            list for gradient calculation
            '''
            variables = self.encoder.variables + self.decoder.variables
            gradients = tape.gradient(loss, variables)
            self.optimizer.apply_gradients(zip(gradients, variables))
        return batch_loss, self.metric.result()


    # Exactly similar as the train step, except that there is no teacher forcing
    @tf.function
    def validation_step(self, input, target, encoder_state):
        loss = 0
        # all computations on graph where gradient is required should be written here.
        with tf.GradientTape() as tape:
            enc_output, enc_state = self.encoder(input, encoder_state)
            # output of encoder to go for decoder as input, whereas, state vector is used for context calculation
            dec_state = enc_state
            #need to setup every input to decoder at first timestep as the start of sentence identifier.
            dec_input = tf.expand_dims([self.target_tokenizer.word_index["\t"]] * self.batch_size, 1)
            '''
            use correct target for previous step as input to the lstm instead of what got decoded, this reduces error propogation
            '''
            for t in range(1, target.shape[1]):
                dec_output, dec_state, _ = self.decoder(dec_input, dec_state, enc_output)
                loss += self.loss(target[:, t], dec_output)
                dec_input = tf.expand_dims(tf.argmax(dec_output, 1), 1)
                self.metric.update_state(target[:, t], dec_output)

            batch_loss = loss / target.shape[1]
            '''
            model.variables returns a list, adding all the variables in the enc-dec model together in a single
            list for gradient calculation
            '''
            variables = self.encoder.variables + self.decoder.variables
            gradients = tape.gradient(loss, variables)
            self.optimizer.apply_gradients(zip(gradients, variables))
        return batch_loss, self.metric.result()

    def set_vocabulary(self, input_tokenizer, target_tokenizer):
        self.input_tokenizer = input_tokenizer
        self.target_tokenizer = target_tokenizer

        encoder_vocab_size = len(self.input_tokenizer.word_index) + 1
        decoder_vocab_size = len(self.target_tokenizer.word_index) + 1

        self.encoder = Encoder(self.rnn_type, self.num_encoder_layers, self.units, encoder_vocab_size,
                               self.embedding_dim, self.dropout)

        self.decoder = Decoder(self.rnn_type, self.num_decoder_layers, self.units, decoder_vocab_size,
                               self.embedding_dim, self.dropout, self.attention_flag)


    def fit(self, dataset, val_dataset, batch_size=128, epochs=10, log_wandb_flag=True):
        self.batch_size = batch_size
        steps_per_epoch = len(dataset) // self.batch_size
        steps_per_epoch_val = len(val_dataset) // self.batch_size

        dataset = dataset.batch(self.batch_size, drop_remainder=True)
        val_dataset = val_dataset.batch(self.batch_size, drop_remainder=True)

        input_lang, target_lang = next(iter(dataset))
        self.max_target_len = input_lang.shape[1]
        self.max_input_len = target_lang.shape[1]


        print("#"*100)
        for epoch in range(1, epochs + 1):
            print(f"EPOCH {epoch}\n")

            total_loss = 0
            total_acc = 0
            self.metric.reset_states()

            enc_state = self.encoder.initialize_hidden_state(self.batch_size)

            print("Training Started...\n")
            for batch, (input, target) in enumerate(dataset.take(steps_per_epoch)):
                batch_loss, acc = self.train_step(input, target, enc_state)
                total_loss += batch_loss
                total_acc += acc
            avg_acc = total_acc / steps_per_epoch
            avg_loss = total_loss / steps_per_epoch

            # Validation loop ##
            total_val_loss = 0
            total_val_acc = 0
            self.metric.reset_states()

            enc_state = self.encoder.initialize_hidden_state(self.batch_size)

            print("\nValidation...")
            for batch, (input, target) in enumerate(val_dataset.take(steps_per_epoch_val)):
                batch_loss, acc = self.validation_step(input, target, enc_state)
                total_val_loss += batch_loss
                total_val_acc += acc

            avg_val_acc = total_val_acc / steps_per_epoch_val
            avg_val_loss = total_val_loss / steps_per_epoch_val

            print("train loss: " + str(avg_loss), "train accuracy: " + str(avg_acc * 100), "val loss: " +
                  str(avg_val_loss), "val accuracy: " + str(avg_val_acc * 100))

            if log_wandb_flag:
                wandb.log({"epoch": epoch,
                               "train loss": avg_loss,
                               "val loss": avg_val_loss,
                               "train acc": avg_acc * 100,
                               "val acc": avg_val_acc * 100})
        print("Model trained")



    def evaluate_model(self, test_dataset, batch_size=None):

        if batch_size is not None:
            self.batch_size = batch_size

        steps_per_epoch_test = len(test_dataset) // batch_size
        test_dataset = test_dataset.batch(batch_size, drop_remainder=True)

        total_test_loss = 0
        total_test_acc = 0
        self.metric.reset_states()

        enc_state = self.encoder.initialize_hidden_state(self.batch_size)

        for batch, (input, target) in enumerate(test_dataset.take(steps_per_epoch_test)):
            batch_loss, acc = self.validation_step(input, target, enc_state)
            total_test_loss += batch_loss
            total_test_acc += acc

        avg_test_acc = total_test_acc / steps_per_epoch_test
        avg_test_loss = total_test_loss / steps_per_epoch_test

        print("Test Loss: " + str(avg_test_loss) + "Test Accuracy: " + str(100*avg_test_acc))

        return avg_test_loss, avg_test_acc

    def translate(self, word, input_tokenizer, targ_tokenizer, gen_heatmap=False):

        word = "\t" + word + "\n"
        inputs = input_tokenizer.texts_to_sequences([word])
        inputs = tf.keras.preprocessing.sequence.pad_sequences(inputs,
                                                               maxlen=self.max_input_len,
                                                               padding="post")
        output = ""
        attention_weights_list = []

        enc_state = self.encoder.initialize_hidden_state(1)
        enc_out, enc_state = self.encoder(inputs, enc_state)

        dec_state = enc_state
        dec_input = tf.expand_dims([targ_tokenizer.word_index["\t"]] * 1, 1)

        for t in range(1, self.max_target_len):
            preds, dec_state, attention_weights = self.decoder(dec_input, dec_state, enc_out)

            if gen_heatmap:
                attention_weights_list.append(attention_weights)

            preds = tf.argmax(preds, 1)
            next_char = targ_tokenizer.index_word[preds.numpy().item() if preds.numpy().item() != 0 else 1]
            output += next_char

            dec_input = tf.expand_dims(preds, 1)

            if next_char == "\n":
                return output[:-1], attention_weights_list[:-1]

        return output[:-1], attention_weights_list[:-1]



# train_with_wandb("hi")
